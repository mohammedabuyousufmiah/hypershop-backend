from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    SENT = "sent"
    DEAD = "dead"


class OutboxMessage(Base):
    """Transactional outbox row.

    A producer (any service inside a UoW) writes one row in the same DB
    transaction as its state change. The ARQ worker polls and dispatches
    messages with at-least-once delivery; consumers must be idempotent.
    """

    __tablename__ = "outbox_messages"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    type: Mapped[str] = mapped_column(String(96), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    status: Mapped[OutboxStatus] = mapped_column(
        String(16),
        nullable=False,
        default=OutboxStatus.PENDING,
        server_default=OutboxStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    available_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','dispatching','sent','dead')",
            name="status_enum",
        ),
        Index("ix_outbox_messages_due", "status", "available_at"),
        Index("ix_outbox_messages_type", "type"),
    )
