"""SQLAlchemy models for referrals."""
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


class ReferralCode(Base):
    __tablename__ = "referral_codes"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, unique=True
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    total_referrals: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    rewarded_referrals: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ReferralAttribution(Base):
    __tablename__ = "referral_attributions"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    referrer_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    referee_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PENDING'")
    )
    rewarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rewarded_by_order_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','REWARDED','EXPIRED')",
            name="ck_referral_attribution_status",
        ),
        Index(
            "ix_referral_attr_referrer",
            "referrer_user_id",
            text("created_at DESC"),
        ),
    )
