"""SQLAlchemy models for loyalty (built 2026-05-11)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class LoyaltyAccount(Base):
    """One row per customer user. Balance + tier are denormalised running
    totals kept consistent by ``LoyaltyService``. Source of truth for
    audit is the ledger (``loyalty_transactions``)."""

    __tablename__ = "loyalty_accounts"

    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    balance_points: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    lifetime_earned_points: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tier: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NONE'")
    )
    tier_recomputed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("balance_points >= 0", name="ck_loyalty_balance_nonneg"),
        CheckConstraint(
            "lifetime_earned_points >= 0", name="ck_loyalty_lifetime_nonneg"
        ),
        CheckConstraint(
            "tier IN ('NONE','BRONZE','SILVER','GOLD')",
            name="ck_loyalty_tier_valid",
        ),
    )


class LoyaltyTransaction(Base):
    """Append-only ledger. ``points`` is signed:
        +EARN (positive)
        -REDEEM / -EXPIRE (negative)
        ±ADJUST (either)

    ``expires_at`` is only set on EARN rows. The service uses it to
    compute "expirable" totals + drives the daily ARQ sweep."""

    __tablename__ = "loyalty_transactions"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    txn_type: Mapped[str] = mapped_column(String(16), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    source_order_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "txn_type IN ('EARN','REDEEM','EXPIRE','ADJUST')",
            name="ck_loyalty_txn_type",
        ),
        CheckConstraint("points <> 0", name="ck_loyalty_points_nonzero"),
        Index(
            "ix_loyalty_txn_user_created",
            "user_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_loyalty_txn_source_order",
            "source_order_id",
            postgresql_where=text("source_order_id IS NOT NULL"),
        ),
    )
