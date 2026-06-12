"""Push service — fan out a notification to all of a user's active devices.

Loads device_tokens for the user, dispatches per-kind via the bound
transport in **parallel** via ``asyncio.gather``, marks dead tokens
inactive in the same DB transaction. The caller (handler) wraps this
in a UoW so the deactivations commit together.

Retry semantics (matters for outbox correctness):
  - Per-device idempotency is guaranteed at the gateway layer (FCM and
    APNS are idempotent on a single token+payload pair within a short
    window — they do their own dedup). On outbox retry, already-
    delivered devices may receive a duplicate push, but content is
    identical so users don't notice. We do NOT track per-device
    delivery in the DB to keep the schema simple.
  - We RAISE ServiceUnavailableError ONLY when: at least one device
    failed transiently AND no devices delivered AND no devices were
    deactivated. (If all tokens were dead, retrying achieves nothing —
    the user needs to re-register a token first; outbox dead-letters.)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceUnavailableError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.modules.mobile.models import DeviceToken
from app.modules.push.transport.push_base import (
    Notification,
    PushOutcome,
    PushSendResult,
)
from app.modules.push.transport.push_registry import get_transport

_logger = get_logger("hypershop.push.service")


@dataclass(frozen=True)
class FanoutResult:
    delivered: int
    invalid_tokens_deactivated: int
    transient_failures: int
    total_devices: int


async def _send_one(dev: DeviceToken, notification: Notification) -> PushSendResult:
    """One-shot send wrapped to never raise — failures map to TRANSIENT
    so an exception in one transport doesn't take out the whole gather."""
    try:
        transport = get_transport(dev.kind)
        return await transport.send(token=dev.token, notification=notification)
    except Exception as e:  # noqa: BLE001  defensive boundary
        _logger.exception(
            "push_transport_unhandled_exception",
            device_id=str(dev.id),
            kind=dev.kind,
        )
        return PushSendResult(
            outcome=PushOutcome.TRANSIENT_FAILURE,
            error_code=f"unhandled_{type(e).__name__}",
            error_message=str(e)[:512],
        )


async def dispatch_to_user(
    *,
    session: AsyncSession,
    user_id: UUID,
    notification: Notification,
) -> FanoutResult:
    """Fan ``notification`` out to all of ``user_id``'s active devices,
    in parallel."""
    rows = (
        await session.execute(
            select(DeviceToken)
            .where(DeviceToken.user_id == user_id)
            .where(DeviceToken.is_active.is_(True)),
        )
    ).scalars().all()

    if not rows:
        _logger.info("push_no_devices", user_id=str(user_id))
        return FanoutResult(0, 0, 0, 0)

    # Parallel dispatch — bounded only by the number of devices for one
    # user (typically 1-5). For broadcast-to-many-users use cases, the
    # caller should chunk users + asyncio.gather across THIS function.
    results = await asyncio.gather(
        *[_send_one(dev, notification) for dev in rows],
    )

    delivered = 0
    invalidated = 0
    transient = 0
    invalid_ids: list[UUID] = []

    now = utc_now()
    for dev, result in zip(rows, results, strict=True):
        if result.outcome == PushOutcome.DELIVERED:
            delivered += 1
            dev.last_seen_at = now
        elif result.outcome == PushOutcome.INVALID_TOKEN:
            invalidated += 1
            invalid_ids.append(dev.id)
            _logger.info(
                "push_token_invalidated",
                user_id=str(user_id),
                device_id=str(dev.id),
                kind=dev.kind,
                code=result.error_code,
            )
        else:
            transient += 1
            _logger.warning(
                "push_transient_failure",
                user_id=str(user_id),
                device_id=str(dev.id),
                kind=dev.kind,
                code=result.error_code,
                message=(result.error_message or "")[:128],
            )

    if invalid_ids:
        await session.execute(
            update(DeviceToken)
            .where(DeviceToken.id.in_(invalid_ids))
            .values(is_active=False),
        )

    # Retry-decision matrix:
    #   delivered>0  → success path (don't raise even if some transient)
    #   invalidated>0 + delivered==0 + transient==0 → all dead tokens;
    #     retrying won't help, return cleanly so outbox marks complete
    #   transient>0 + delivered==0 + invalidated==0 → genuine "all
    #     gateways down for this user", raise so outbox retries
    #   transient>0 + delivered==0 + invalidated>0 → partial — at
    #     least one was dead. Don't retry — the live tokens that did
    #     fail transiently will get the next event anyway, and we'd
    #     spam the now-inactive devices on retry. Return cleanly.
    if transient > 0 and delivered == 0 and invalidated == 0:
        raise ServiceUnavailableError(
            "All push devices failed transiently for user.",
            details={
                "user_id": str(user_id),
                "devices": len(rows),
                "transient": transient,
            },
        )

    return FanoutResult(
        delivered=delivered,
        invalid_tokens_deactivated=invalidated,
        transient_failures=transient,
        total_devices=len(rows),
    )
