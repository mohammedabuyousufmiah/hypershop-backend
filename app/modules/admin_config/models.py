"""SQLAlchemy models for per-module runtime config tables.

Both tables are non-versioned admin-tier config — there's no historical
ledger here (audit_logs already records every change via the endpoint
layer's record_audit calls). If point-in-time reconstruction is later
required, add a journal table that tails these via a trigger or
outbox; don't add row-level history columns.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class ModuleSetting(Base):
    """Typed key-value scoped to a module."""
    __tablename__ = "module_settings"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    setting_key: Mapped[str] = mapped_column(String(96), nullable=False)
    value: Mapped[object | None] = mapped_column(JSONB, nullable=False, default=None)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False, default="json")
    description: Mapped[str | None] = mapped_column(String(512))
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("module_key", "setting_key",
                         name="uq_module_settings_module_setting"),
        CheckConstraint(
            "value_type IN ('string','number','boolean','json')",
            name="module_settings_value_type_enum",
        ),
    )


class ModuleFeatureFlag(Base):
    """Boolean toggle with optional gradual rollout percent."""
    __tablename__ = "module_feature_flags"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    flag_key: Mapped[str] = mapped_column(String(96), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollout_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    description: Mapped[str | None] = mapped_column(String(512))
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("module_key", "flag_key",
                         name="uq_module_feature_flags_module_flag"),
        CheckConstraint(
            "rollout_percent >= 0 AND rollout_percent <= 100",
            name="module_feature_flags_rollout_range",
        ),
    )
