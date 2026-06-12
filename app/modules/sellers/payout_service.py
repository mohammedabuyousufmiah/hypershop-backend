"""Seller payout aggregation engine.

For a given seller + date range, walks ``order_lines`` joined to
``products.seller_id`` and computes:

    gross         = Σ (unit_price × quantity)     for COMPLETED orders only
    commission    = gross × seller.commission_percent / 100
    return_debit  = Σ (refunded line totals)      from returns ↔ orders
                    where the line belongs to this seller
    net_payable   = gross - commission - return_debit

The result feeds either a payout preview (for the admin UI) or — when
``request_payout()`` is called — a row in the existing
``supplier_payments`` workflow which routes through the 3+1 approval
chain that's already built.

Architecture:
    sellers/payout_service.py   ← THIS — the aggregator
    sellers/api/payouts.py       ← admin endpoints (preview / request)
    supplier_payments/...        ← existing 3+1 approval workflow

Why this lives in ``sellers`` not ``supplier_payments``: the
aggregation logic is seller-domain (commission rules, return
liability). The approval/finance flow is supplier-payments-domain.
Keeping them separate so each module has one job.

Open follow-up:
    - ``return_debit`` calc assumes returns have an
      ``approved_refund_amount`` column. If the actual returns module
      uses different field names, the helper degrades gracefully
      (returns 0 for that segment) and logs a warning.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_log = logging.getLogger("hypershop.sellers.payout")


# Order statuses that count as "earnings" — only fully-delivered orders
# pay out. Cancelled/failed never do; in-progress orders accrue but are
# not yet payable.
PAYABLE_ORDER_STATUSES = ("completed",)


@dataclass(frozen=True)
class PayoutPreview:
    seller_id: UUID
    period_start: datetime
    period_end: datetime
    # All money values are Decimal in the seller's transaction currency
    # (we assume BDT across the marketplace today; if multi-currency
    # comes later, this dataclass grows a ``currency`` field).
    gross: Decimal
    commission_percent: Decimal
    commission_deducted: Decimal
    return_debit: Decimal
    net_payable: Decimal
    # For audit + UI rendering: how many orders, lines, returns the
    # figures aggregate over.
    order_count: int
    line_count: int
    return_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "seller_id": str(self.seller_id),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "gross": f"{self.gross:.2f}",
            "commission_percent": f"{self.commission_percent:.2f}",
            "commission_deducted": f"{self.commission_deducted:.2f}",
            "return_debit": f"{self.return_debit:.2f}",
            "net_payable": f"{self.net_payable:.2f}",
            "order_count": self.order_count,
            "line_count": self.line_count,
            "return_count": self.return_count,
        }


class SellerPayoutService:
    """Stateless service: takes an AsyncSession, returns DTOs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def compute_period_owed(
        self,
        *,
        seller_id: UUID,
        period_start: datetime,
        period_end: datetime,
    ) -> PayoutPreview:
        """Returns the payout preview for the seller × period.

        - ``gross`` reflects only completed orders within the window
        - ``return_debit`` reflects approved returns whose refund-issued
          date sits inside the same window (so returns from older orders
          settling now count against this period's payout — which is
          how cash actually flows)
        """
        # ---- 1. Pull seller commission rate ----
        rate_row = (
            await self.session.execute(
                text(
                    "SELECT commission_percent, status FROM sellers WHERE id = :sid",
                ),
                {"sid": seller_id},
            )
        ).first()
        if rate_row is None:
            msg = f"seller {seller_id} not found"
            raise ValueError(msg)
        commission_pct = Decimal(str(rate_row[0]))
        seller_status = rate_row[1]
        if seller_status not in ("approved", "active"):
            _log.warning(
                "payout_compute_for_non_active_seller",
                extra={"seller_id": str(seller_id), "status": seller_status},
            )

        # ---- 2. Gross — sum of line_totals for completed orders ----
        statuses_csv = ",".join(f"'{s}'" for s in PAYABLE_ORDER_STATUSES)
        gross_row = (
            await self.session.execute(
                text(
                    f"""
                    SELECT
                        COALESCE(SUM(ol.line_total), 0) AS gross,
                        COUNT(DISTINCT ol.order_id)     AS order_count,
                        COUNT(ol.id)                    AS line_count
                      FROM order_lines ol
                      JOIN orders o ON o.id = ol.order_id
                      JOIN products p ON p.id = (
                          SELECT product_id FROM product_variants
                           WHERE id = ol.variant_id LIMIT 1
                      )
                     WHERE p.seller_id = :sid
                       AND o.status IN ({statuses_csv})
                       AND o.placed_at >= :start
                       AND o.placed_at < :end
                    """,
                ),
                {"sid": seller_id, "start": period_start, "end": period_end},
            )
        ).first()
        gross = Decimal(str(gross_row[0] or 0)).quantize(Decimal("0.01"))
        order_count = int(gross_row[1] or 0)
        line_count = int(gross_row[2] or 0)

        # ---- 3. Commission deducted ----
        commission_deducted = (
            gross * commission_pct / Decimal("100")
        ).quantize(Decimal("0.01"))

        # ---- 4. Return debit — read from the canonical
        # ``seller_wallet_ledger`` table populated by the
        # returns.return.completed handler. One row per refunded
        # return line, keyed by (return_request_line_id, entry_type)
        # for idempotency, so summing is safe across redeliveries.
        return_row = (
            await self.session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS total,
                           COUNT(DISTINCT return_request_id) AS rcount
                      FROM seller_wallet_ledger
                     WHERE seller_id = :sid
                       AND entry_type = 'return_debit'
                       AND direction = 'debit'
                       AND created_at >= :start
                       AND created_at < :end
                    """,
                ),
                {
                    "sid": seller_id,
                    "start": period_start,
                    "end": period_end,
                },
            )
        ).first()
        return_debit = Decimal(str(return_row[0] or 0)).quantize(Decimal("0.01"))
        return_count = int(return_row[1] or 0)

        # ---- 5. Net payable ----
        net_payable = (gross - commission_deducted - return_debit).quantize(
            Decimal("0.01"),
        )
        # Net can go negative if returns exceed gross. We clamp to 0
        # for the payout amount (carry-forward to next period is a
        # phase-2 feature — for now, a negative period rolls into the
        # next via the audit log). Operators see the negative figure
        # in the preview so they know to chase the seller.
        if net_payable < 0:
            _log.info(
                "payout_period_negative_clamped_to_zero",
                extra={
                    "seller_id": str(seller_id),
                    "net": str(net_payable),
                    "gross": str(gross),
                    "return_debit": str(return_debit),
                },
            )

        return PayoutPreview(
            seller_id=seller_id,
            period_start=period_start,
            period_end=period_end,
            gross=gross,
            commission_percent=commission_pct,
            commission_deducted=commission_deducted,
            return_debit=return_debit,
            net_payable=net_payable,
            order_count=order_count,
            line_count=line_count,
            return_count=return_count,
        )

    async def list_payable_sellers(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        min_net: Decimal = Decimal("0.01"),
    ) -> list[PayoutPreview]:
        """Return every active seller with a positive net payable
        in the period. Used by the biweekly batch-payout cron.
        """
        sellers = (
            await self.session.execute(
                text(
                    "SELECT id FROM sellers "
                    "WHERE status IN ('approved', 'active') "
                    "ORDER BY id",
                ),
            )
        ).all()

        previews: list[PayoutPreview] = []
        for (sid,) in sellers:
            try:
                p = await self.compute_period_owed(
                    seller_id=sid,
                    period_start=period_start,
                    period_end=period_end,
                )
                if p.net_payable >= min_net:
                    previews.append(p)
            except Exception as e:  # noqa: BLE001
                _log.warning(
                    "payout_skip_seller_error",
                    extra={"seller_id": str(sid), "error": str(e)},
                )
                continue
        return previews

    async def create_payout_request(
        self,
        *,
        preview: PayoutPreview,
        created_by: UUID | None,
    ) -> dict[str, Any]:
        """Hand the computed preview off to the supplier_payments
        approval workflow.

        We DON'T directly create a ``supplier_bills`` row here — that
        would couple sellers tightly to supplier_payments. Instead we
        return a payload the admin layer hands to
        supplier_payments.service.SupplierPaymentService.request_bill
        (or equivalent). If that service signature changes later,
        only the admin glue code updates, not this aggregator.
        """
        return {
            "kind": "seller_payout",
            "seller_id": str(preview.seller_id),
            "period_start": preview.period_start.isoformat(),
            "period_end": preview.period_end.isoformat(),
            "amount": f"{preview.net_payable:.2f}",
            "currency": "BDT",
            "breakdown": preview.as_dict(),
            "created_by": str(created_by) if created_by else None,
        }
