from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, String, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class AuditLog(Base):
    """Append-only audit log. Every row is immutable; the read API exposes
    list/search but never update or delete. Migration grants prevent UPDATE
    and DELETE at the DB level so even a service-layer bug cannot mutate.
    """

    __tablename__ = "audit_logs"  # renamed from "audit_log" in alembic 0063

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    actor_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        Index("ix_audit_log_occurred_at", "occurred_at"),
        Index("ix_audit_log_actor_id", "actor_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_resource", "resource_type", "resource_id"),
    )
