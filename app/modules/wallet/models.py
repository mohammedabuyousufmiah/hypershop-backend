"""Hypershop e-commerce wallet ORM mappings.

Tables are prefixed ``hypershop_`` by convention.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class HypershopWallet(Base):
    __tablename__ = "hypershop_wallets"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, unique=True,
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'BDT'"),
    )
    balance_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'"),
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "balance_minor >= 0",
            name="ck_hypershop_wallet_balance_nonneg",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','FROZEN','CLOSED')",
            name="ck_hypershop_wallet_status",
        ),
    )


class HypershopWalletTxn(Base):
    __tablename__ = "hypershop_wallet_txns"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    wallet_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    source_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    memo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "amount_minor > 0",
            name="ck_hypershop_wallet_txn_amount_positive",
        ),
        CheckConstraint(
            "balance_after_minor >= 0",
            name="ck_hypershop_wallet_txn_balance_nonneg",
        ),
        CheckConstraint(
            "kind IN ('credit','debit','adjust')",
            name="ck_hypershop_wallet_txn_kind",
        ),
        Index(
            "ix_hypershop_wallet_txn_wallet_at",
            "wallet_id",
            text("created_at DESC"),
        ),
    )
