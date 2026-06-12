"""rider_kyc_submissions ORM — one row per rider user."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class RiderKycSubmission(Base):
    __tablename__ = "rider_kyc_submissions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    profile_photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    nid_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nid_front_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    nid_back_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    mobile_primary: Mapped[str] = mapped_column(String(32), nullable=False)
    mobile_secondary: Mapped[str | None] = mapped_column(String(32), nullable=True)
    house_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flat_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    area: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reference_nid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending",
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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
            "status IN ('pending','verified','rejected')",
            name="ck_rider_kyc_status",
        ),
        Index("ix_rider_kyc_status", "status", "created_at"),
    )
