"""SQLAlchemy models for affiliates."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class AffiliateRecord(Base):
    __tablename__ = "affiliate_records"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, unique=True
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("0.05")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    total_conversions: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_commission_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 1",
            name="ck_affiliate_commission_rate_range",
        ),
    )
