"""Seller rating ORM models — current snapshot + history table."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class HypershopSellerRating(Base):
    __tablename__ = "hypershop_seller_ratings"

    seller_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    overall_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("50.00"),
    )
    tier: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'standard'"),
    )
    on_time_shipping_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True,
    )
    return_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True,
    )
    dispute_resolution_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True,
    )
    response_time_hours: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 2), nullable=True,
    )
    review_avg: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), nullable=True,
    )
    review_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    order_count_30d: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    last_computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
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
            "overall_score BETWEEN 0 AND 100",
            name="ck_hypershop_seller_ratings_score_range",
        ),
        CheckConstraint(
            "tier IN ('platinum','gold','silver','standard','poor','suspended')",
            name="ck_hypershop_seller_ratings_tier",
        ),
        Index(
            "ix_hypershop_seller_ratings_score_desc",
            text("overall_score DESC"),
        ),
        Index("ix_hypershop_seller_ratings_tier", "tier"),
    )


class HypershopSellerRatingSnapshot(Base):
    __tablename__ = "hypershop_seller_rating_snapshots"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    seller_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
    )
    overall_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False,
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index(
            "ix_hypershop_seller_rating_snapshots_seller_at",
            "seller_id", text("computed_at DESC"),
        ),
    )


__all__ = [
    "HypershopSellerRating",
    "HypershopSellerRatingSnapshot",
]
