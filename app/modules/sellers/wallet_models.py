"""Seller wallet ledger ORM model — append-only, one row per liability event.

Created 2026-05-13 to back the seller-liability flow:
- When a customer return is COMPLETED, the seller owning the variant
  is debited an amount equal to the order line total. The payout
  aggregator later subtracts the sum of ``return_debit`` rows from the
  seller's gross earnings for the period.

Idempotency is enforced by a partial unique index on
``(return_request_line_id, entry_type)`` — see migration
0046_seller_wallet_ledger.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class SellerWalletLedger(Base):
    __tablename__ = "seller_wallet_ledger"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    seller_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_type: Mapped[str] = mapped_column(String(length=32), nullable=False)
    direction: Mapped[str] = mapped_column(String(length=8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    order_line_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    return_request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    return_request_line_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "entry_type IN ("
            "'return_debit',"
            "'commission_debit',"
            "'payout_credit',"
            "'manual_adjustment'"
            ")",
            name="ck_seller_wallet_ledger_entry_type",
        ),
        CheckConstraint(
            "direction IN ('debit', 'credit')",
            name="ck_seller_wallet_ledger_direction",
        ),
        CheckConstraint(
            "amount >= 0",
            name="ck_seller_wallet_ledger_amount_nonneg",
        ),
        Index(
            "ix_seller_wallet_ledger_seller_created",
            "seller_id",
            "created_at",
        ),
    )
