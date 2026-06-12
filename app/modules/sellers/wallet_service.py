"""Seller wallet (liability ledger) service.

Currently a single responsibility: post a ``return_debit`` row when a
customer return is COMPLETED, so the seller's payout for the period
is reduced by the refunded amount.

Idempotency: the underlying table has a partial unique index on
``(return_request_line_id, entry_type)`` so re-delivery of the
``returns.return.completed`` outbox event is safe — the second post
no-ops via ``ON CONFLICT DO NOTHING``.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.logging import get_logger
from app.core.security.principal import Principal

_log = get_logger("hypershop.sellers.wallet")


class SellerWalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def debit_for_return(
        self,
        *,
        seller_id: UUID,
        amount: Decimal,
        order_id: UUID,
        order_line_id: UUID,
        return_request_id: UUID,
        return_request_line_id: UUID,
        principal: Principal,
        note: str | None = None,
    ) -> UUID | None:
        """Post a ``return_debit`` ledger row.

        Returns the new row id, or ``None`` if a row already existed
        (idempotent no-op). Amount must be ≥ 0.
        """
        if amount < 0:
            raise ValueError("debit amount must be >= 0")

        # ON CONFLICT DO NOTHING against the partial unique index on
        # (return_request_line_id, entry_type) — second delivery of
        # the outbox event short-circuits cleanly.
        row = (
            await self.session.execute(
                text(
                    """
                    INSERT INTO seller_wallet_ledger (
                        seller_id, entry_type, direction, amount,
                        order_id, order_line_id,
                        return_request_id, return_request_line_id,
                        note, created_by
                    ) VALUES (
                        :seller_id, 'return_debit', 'debit', :amount,
                        :order_id, :order_line_id,
                        :return_request_id, :return_request_line_id,
                        :note, :created_by
                    )
                    ON CONFLICT (return_request_line_id, entry_type)
                        WHERE return_request_line_id IS NOT NULL
                        DO NOTHING
                    RETURNING id
                    """,
                ),
                {
                    "seller_id": seller_id,
                    "amount": amount,
                    "order_id": order_id,
                    "order_line_id": order_line_id,
                    "return_request_id": return_request_id,
                    "return_request_line_id": return_request_line_id,
                    "note": note,
                    # SystemPrincipal has no user_id — outbox-driven
                    # handlers run as ``system`` so created_by is NULL.
                    "created_by": getattr(principal, "user_id", None),
                },
            )
        ).first()
        new_id = row[0] if row else None

        await record_audit(
            actor=principal,
            action="sellers.wallet.return_debit"
            + ("" if new_id else "_idempotent_noop"),
            resource_type="seller_wallet_ledger",
            resource_id=new_id,
            metadata={
                "seller_id": str(seller_id),
                "amount": str(amount),
                "return_request_line_id": str(return_request_line_id),
                "order_line_id": str(order_line_id),
            },
        )
        if new_id is None:
            _log.info(
                "seller_return_debit_idempotent_noop",
                seller_id=str(seller_id),
                return_request_line_id=str(return_request_line_id),
            )
        else:
            _log.info(
                "seller_return_debit_posted",
                seller_id=str(seller_id),
                ledger_id=str(new_id),
                amount=str(amount),
                return_request_line_id=str(return_request_line_id),
            )
        return new_id
