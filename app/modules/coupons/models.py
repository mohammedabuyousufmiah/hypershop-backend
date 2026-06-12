"""Coupons + redemptions."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discount_type: Mapped[str] = mapped_column(String(16), nullable=False)
    discount_value_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    min_subtotal_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    max_discount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_total_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_uses_per_customer: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    total_uses: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "discount_type IN ('PERCENT','FIXED')",
            name="ck_coupon_discount_type",
        ),
        CheckConstraint(
            "discount_value_minor > 0", name="ck_coupon_discount_positive"
        ),
    )


class CouponRedemption(Base):
    __tablename__ = "coupon_redemptions"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    coupon_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("coupons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    customer_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )
    order_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    discount_applied_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "order_id", "coupon_id", name="uq_coupon_redemption_order_coupon"
        ),
        Index(
            "ix_coupon_redemptions_customer",
            "coupon_id",
            "customer_user_id",
        ),
    )
