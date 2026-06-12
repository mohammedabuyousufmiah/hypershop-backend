"""MobileDeviceSession — per-device PIN/biometric quick-login state.

One row per (user_id, device_id, app_type). Migration 0097.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class MobileDeviceSession(Base, TimestampMixin):
    __tablename__ = "mobile_device_sessions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    app_type: Mapped[str] = mapped_column(String(24), nullable=False, server_default="rider_app")
    platform: Mapped[str] = mapped_column(String(16), nullable=False, server_default="android")
    device_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pin_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    pin_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    biometric_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(nullable=True)
    last_unlock_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint(
            "user_id", "device_id", "app_type",
            name="uq_mobile_device_sessions_user_device_app",
        ),
        Index("ix_mobile_device_sessions_user", "user_id", "app_type"),
    )
