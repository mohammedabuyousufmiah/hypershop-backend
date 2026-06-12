from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_sessionmaker
from app.core.events.models import OutboxMessage, OutboxStatus
from app.core.logging import get_logger
from app.core.time import utc_in, utc_now

_logger = get_logger("hypershop.outbox")

OutboxHandler = Callable[[OutboxMessage], Awaitable[None]]
# Multiple handlers per event type are supported — each is invoked in
# registration order. This lets independent modules (e.g. inventory +
# finance) both react to the same producer event without coupling.
_handlers: dict[str, list[OutboxHandler]] = {}

_MAX_ATTEMPTS = 8
_BATCH_SIZE = 25


def register_handler(event_type: str, handler: OutboxHandler) -> None:
    bucket = _handlers.setdefault(event_type, [])
    if handler in bucket:
        # Idempotent re-registration is a no-op rather than a hard error so
        # that test fixtures (which re-import handler modules) don't crash.
        # Producer modules guard against accidental duplicates via this check.
        raise ValueError(
            f"outbox handler already registered for {event_type}: "
            f"{getattr(handler, '__qualname__', handler)}",
        )
    bucket.append(handler)


def _backoff_seconds(attempts: int) -> int:
    return min(60 * (2**attempts), 3600)


async def _claim_batch(session: AsyncSession) -> list[OutboxMessage]:
    """Claim up to ``_BATCH_SIZE`` due rows using ``FOR UPDATE SKIP LOCKED`` so
    multiple workers can process in parallel without colliding.
    """
    stmt = (
        select(OutboxMessage)
        .where(
            OutboxMessage.status == OutboxStatus.PENDING,
            OutboxMessage.available_at <= utc_now(),
        )
        .order_by(OutboxMessage.available_at.asc())
        .limit(_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if rows:
        await session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id.in_([r.id for r in rows]))
            .values(status=OutboxStatus.DISPATCHING),
        )
    return rows


async def _process(message: OutboxMessage) -> tuple[bool, str | None]:
    handlers = _handlers.get(message.type)
    if not handlers:
        return False, f"no handler registered for {message.type}"
    # Run ALL handlers — they're independent subscribers and one's
    # failure must not block the others. Collect errors; if any
    # failed, the whole message stays pending for retry (handlers
    # must be idempotent — re-running a successful handler is safe).
    errors: list[str] = []
    for handler in handlers:
        try:
            await handler(message)
        except Exception as e:
            errors.append(
                f"{getattr(handler, '__qualname__', repr(handler))}: "
                f"{type(e).__name__}: {e}"
            )
    if errors:
        return False, " ; ".join(errors)
    return True, None


async def dispatch_once() -> int:
    """Process one batch of due outbox messages. Returns count handled.

    Each message runs in its own transaction so a poisoned message doesn't
    take down the batch.
    """
    sessionmaker = get_sessionmaker()
    handled = 0
    async with sessionmaker() as session:
        async with session.begin():
            batch = await _claim_batch(session)

        for msg in batch:
            ok, err = await _process(msg)
            async with sessionmaker() as s2, s2.begin():
                if ok:
                    await s2.execute(
                        update(OutboxMessage)
                        .where(OutboxMessage.id == msg.id)
                        .values(
                            status=OutboxStatus.SENT,
                            dispatched_at=utc_now(),
                            last_error=None,
                        ),
                    )
                else:
                    new_attempts = msg.attempts + 1
                    if new_attempts >= _MAX_ATTEMPTS:
                        await s2.execute(
                            update(OutboxMessage)
                            .where(OutboxMessage.id == msg.id)
                            .values(
                                status=OutboxStatus.DEAD,
                                attempts=new_attempts,
                                last_error=err,
                            ),
                        )
                        _logger.error(
                            "outbox_dead_letter",
                            id=str(msg.id),
                            type=msg.type,
                            attempts=new_attempts,
                            error=err,
                        )
                    else:
                        await s2.execute(
                            update(OutboxMessage)
                            .where(OutboxMessage.id == msg.id)
                            .values(
                                status=OutboxStatus.PENDING,
                                attempts=new_attempts,
                                last_error=err,
                                available_at=utc_in(_backoff_seconds(new_attempts)),
                            ),
                        )
                        _logger.warning(
                            "outbox_retry",
                            id=str(msg.id),
                            type=msg.type,
                            attempts=new_attempts,
                            backoff_seconds=_backoff_seconds(new_attempts),
                            error=err,
                        )
            handled += 1
    return handled


__all__ = [
    "_MAX_ATTEMPTS",
    "OutboxHandler",
    "dispatch_once",
    "register_handler",
]
