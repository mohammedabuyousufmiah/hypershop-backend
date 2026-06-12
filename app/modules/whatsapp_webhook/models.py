"""ORM model for WhatsApp delivery status updates.

One row per (wamid, status) pair. Same wamid receives multiple status
updates in normal Meta lifecycle: ``sent`` → ``delivered`` → ``read``.
We store every transition for ops visibility (e.g. "show messages
that delivered but were never read in 24h").

Idempotency:
  UNIQUE on (wamid, status). Meta retries deliveries on any non-2xx,
  so duplicate (wamid, status) pairs are a normal occurrence — the
  unique constraint silently no-ops the duplicate via INSERT ... ON
  CONFLICT DO NOTHING in the repository.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class WhatsAppMessageStatus(Base, TimestampMixin):
    __tablename__ = "whatsapp_message_statuses"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Meta's wamid (e.g. "wamid.HBgLODgwMTk...="). One per outbound
    # message. NOT a UUID — opaque string from Meta.
    wamid: Mapped[str] = mapped_column(String(128), nullable=False)
    # 'sent' | 'delivered' | 'read' | 'failed' | 'deleted'
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_msisdn: Mapped[str] = mapped_column(String(20), nullable=False)
    # Meta's "timestamp" field (Unix epoch as string in their payload).
    status_timestamp: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    # When status='failed', Meta supplies an errors[] array with code +
    # title + (sometimes) message + href. We store the FIRST error.
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Verbatim webhook event chunk (the matched ``statuses[i]`` object)
    # — kept for replay + debugging.
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        UniqueConstraint(
            "wamid", "status",
            name="uq_whatsapp_message_statuses_wamid_status",
        ),
        CheckConstraint(
            "status IN ('sent','delivered','read','failed','deleted')",
            name="ck_whatsapp_message_statuses_status_enum",
        ),
        Index(
            "ix_whatsapp_message_statuses_wamid",
            "wamid",
        ),
        Index(
            "ix_whatsapp_message_statuses_recipient_received",
            "recipient_msisdn", "received_at",
        ),
        Index(
            "ix_whatsapp_message_statuses_failed_received",
            "status", "received_at",
            postgresql_where=text("status = 'failed'"),
        ),
    )
