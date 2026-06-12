from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, Integer, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class IdempotencyKey(Base, TimestampMixin):
    """Persisted idempotency-key record. Lives for ~24h then is reaped.

    Stored separately from per-resource state so a replay produces the same
    response (status + body hash) even after the row is committed.
    """

    __tablename__ = "idempotency_keys"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        Index(
            "uq_idempotency_keys_actor_method_path_key",
            "actor_id",
            "method",
            "path",
            "key",
            unique=True,
        ),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )
