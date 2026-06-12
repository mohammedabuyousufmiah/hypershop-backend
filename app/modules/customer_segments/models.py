"""ORM mappings for customer_segments — RFM scores + segments + memberships."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class HypershopCustomerRfmScore(Base):
    __tablename__ = "hypershop_customer_rfm_scores"

    customer_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    recency_days: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency_count: Mapped[int] = mapped_column(Integer, nullable=False)
    monetary_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recency_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    frequency_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    monetary_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rfm_total: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    segment_code: Mapped[str] = mapped_column(String(32), nullable=False)
    last_order_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "recency_score BETWEEN 1 AND 5",
            name="ck_rfm_recency_score_range",
        ),
        CheckConstraint(
            "frequency_score BETWEEN 1 AND 5",
            name="ck_rfm_frequency_score_range",
        ),
        CheckConstraint(
            "monetary_score BETWEEN 1 AND 5",
            name="ck_rfm_monetary_score_range",
        ),
        CheckConstraint(
            "rfm_total BETWEEN 3 AND 15",
            name="ck_rfm_total_range",
        ),
        Index(
            "ix_rfm_scores_segment_computed",
            "segment_code", text("computed_at DESC"),
        ),
        Index(
            "ix_rfm_scores_total_desc",
            text("rfm_total DESC"),
        ),
    )


class HypershopCustomerSegment(Base):
    __tablename__ = "hypershop_customer_segments"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    name_bn: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    rule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    estimated_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
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
        Index(
            "ix_customer_segments_active_code",
            "is_active", "code",
        ),
    )


class HypershopCustomerSegmentMembership(Base):
    __tablename__ = "hypershop_customer_segment_memberships"

    segment_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "hypershop_customer_segments.id", ondelete="CASCADE",
        ),
        primary_key=True,
    )
    customer_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index(
            "ix_segment_memberships_customer_added",
            "customer_user_id", text("added_at DESC"),
        ),
    )


__all__ = [
    "HypershopCustomerRfmScore",
    "HypershopCustomerSegment",
    "HypershopCustomerSegmentMembership",
]
