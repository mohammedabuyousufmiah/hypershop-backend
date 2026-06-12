"""Outbox handlers for the sellers module.

Currently one handler:
  ``returns.return.completed`` → walk every return line, look up the
  variant's seller, and post a ``return_debit`` ledger row equal to
  the refunded line total.

The event payload from ``returns.service`` only carries identifiers
(return_request_id, order_id) — the handler loads the request +
order lines + product seller_id on its own to derive amount + seller.

Idempotent — the underlying ledger insert is ON CONFLICT DO NOTHING.
"""
from __future__ import annotations

import contextlib
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.core.security.principal import SystemPrincipal
from app.modules.returns.events import EVT_RETURN_COMPLETED
from app.modules.sellers.wallet_service import SellerWalletService

_log = get_logger("hypershop.sellers.handlers")


async def _handle_return_completed(message: OutboxMessage) -> None:
    """For each return line, debit the owning seller by the refunded
    line total (= order_line.unit_price * inspected_quantity).
    """
    payload = message.payload or {}
    return_request_id_str = payload.get("return_request_id")
    if not return_request_id_str:
        _log.warning("return_completed_payload_missing_request_id")
        return
    return_request_id = UUID(return_request_id_str)

    async with UnitOfWork().transactional() as session:
        # Pull each return line + its order_line + its product.seller_id
        # in one query. Filter to lines that physically arrived
        # (inspected_quantity > 0) — a zero-quantity line wasn't
        # actually returned, so no refund, no seller debit.
        rows = (
            await session.execute(
                text(
                    """
                    SELECT
                        rrl.id              AS return_line_id,
                        rrl.order_line_id   AS order_line_id,
                        rrl.inspected_quantity AS inspected_qty,
                        ol.unit_price       AS unit_price,
                        ol.order_id         AS order_id,
                        p.seller_id         AS seller_id
                      FROM return_request_lines rrl
                      JOIN order_lines      ol ON ol.id = rrl.order_line_id
                      JOIN product_variants pv ON pv.id = ol.variant_id
                      JOIN products         p  ON p.id  = pv.product_id
                     WHERE rrl.return_request_id = :rid
                       AND COALESCE(rrl.inspected_quantity, 0) > 0
                    """,
                ),
                {"rid": return_request_id},
            )
        ).all()

        svc = SellerWalletService(session)
        principal = SystemPrincipal()
        posted = 0
        skipped_no_seller = 0
        for r in rows:
            seller_id = r.seller_id
            if seller_id is None:
                # First-party product (Hypershop owns it) — no seller
                # to debit. Skip silently; this is the common case for
                # legacy items.
                skipped_no_seller += 1
                continue
            amount = (
                Decimal(str(r.unit_price)) * Decimal(int(r.inspected_qty))
            ).quantize(Decimal("0.01"))
            await svc.debit_for_return(
                seller_id=seller_id,
                amount=amount,
                order_id=r.order_id,
                order_line_id=r.order_line_id,
                return_request_id=return_request_id,
                return_request_line_id=r.return_line_id,
                principal=principal,
                note=f"Return {payload.get('code', '')} line refund",
            )
            posted += 1

    _log.info(
        "sellers_return_debits_posted",
        return_request_id=str(return_request_id),
        posted=posted,
        skipped_no_seller=skipped_no_seller,
    )


def register_seller_handlers() -> None:
    """Idempotent — safe under test fixture reloads."""
    with contextlib.suppress(ValueError):
        register_handler(EVT_RETURN_COMPLETED, _handle_return_completed)


register_seller_handlers()
