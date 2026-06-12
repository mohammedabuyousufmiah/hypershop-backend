from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.uow import current_session
from app.core.events.models import OutboxMessage, OutboxStatus
from app.core.events.types import OutboxEnvelope
from app.core.time import utc_now


async def enqueue_outbox(
    *,
    type: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    available_at: datetime | None = None,
    session: AsyncSession | None = None,
) -> OutboxMessage:
    """Enqueue an outbox message inside the active transaction.

    The ARQ worker polls ``outbox_messages`` and dispatches in pending order.
    Producers MUST call this from within ``UnitOfWork.transactional()`` so the
    enqueue commits atomically with the source state change.
    """
    sess = session or current_session()
    envelope = OutboxEnvelope(type=type, payload=payload, metadata=metadata or {})
    msg = OutboxMessage(
        type=envelope.type,
        payload=envelope.payload,
        metadata_=envelope.metadata,
        status=OutboxStatus.PENDING,
        available_at=available_at or utc_now(),
    )
    sess.add(msg)
    await sess.flush()
    return msg
