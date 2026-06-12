"""Analytics events — append-only browser/app event ingest."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    anonymous_id: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    properties: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index(
            "ix_analytics_events_user_created",
            "customer_user_id",
            text("created_at DESC"),
            postgresql_where=text("customer_user_id IS NOT NULL"),
        ),
        Index(
            "ix_analytics_events_type_created",
            "event_type",
            text("created_at DESC"),
        ),
    )
