"""Outbox handlers that forward voice.call.* events to the agent SSE bus.

Producers live in ``app/modules/customer_care/voice_calls.py`` — every
state transition (ringing → assigned → in_call → ended/missed) enqueues
one outbox message. This module consumes those messages and pushes them
onto the in-process SSE bus (``sse_bus``), which feeds the
``GET /customer-care/voice-calls/stream`` endpoint.

Targeting:
- Events with an ``agent_id`` are pushed to that agent's queue PLUS the
  broadcast queue (so dispatcher dashboards stay informed).
- Ringing events with no assignee are broadcast-only.

Multi-pod caveat: ``sse_bus`` is in-process. For multi-replica prod the
right move is a Redis pub/sub bridge — out of scope here; tracked
separately. Single-pod dev + small prod is fine.

Idempotent registration. Importing this module is the side effect that
hooks the handler into the global outbox dispatcher.
"""
from __future__ import annotations

import contextlib
from typing import Any
from uuid import UUID

from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.modules.customer_care import sse_bus

_log = get_logger("hypershop.customer_care.voice_handlers")

# Event names produced by ``voice_calls.py`` — keep in sync with the
# _ALLOWED transition table there.
_VOICE_EVENT_TYPES: tuple[str, ...] = (
    "voice.call.ringing",
    "voice.call.assigned",
    "voice.call.answered",
    "voice.call.released",
    "voice.call.ended",
    "voice.call.missed",
)


def _coerce_agent_id(raw: Any) -> UUID | None:
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def _handle_voice_call_event(message: OutboxMessage) -> None:
    """Forward one outbox message to both SSE buses.

    The payload shape matches what ``voice_calls._transition`` enqueues
    (voice_call_id, provider, provider_call_id, from_phone, customer_id,
    agent_id, from_status, to_status). We add ``type`` so the FE can
    discriminate without parsing the channel name.
    """
    payload = dict(message.payload or {})
    event = {"type": message.type, **payload}
    agent_id = _coerce_agent_id(payload.get("agent_id"))

    # In-process bus — fans to the agent's queue + broadcast queue.
    sse_bus.publish(event, agent_id=agent_id, broadcast=True)

    # Cross-pod fan-out via Redis. No-op if REDIS_URL is unreachable;
    # silently skipped if not running inside an event loop. Tagged with
    # this pod's id so the consumer loop skips its own publishes.
    try:
        from app.modules.customer_care import sse_redis_bridge
        sse_redis_bridge.publish_voice_event(event)
    except Exception as e:  # noqa: BLE001
        _log.warning("voice_event_redis_bridge_failed", error=str(e))

    _log.info(
        "voice_event_forwarded",
        type=message.type,
        agent_id=str(agent_id) if agent_id else None,
        voice_call_id=payload.get("voice_call_id"),
    )


def register_voice_handlers() -> None:
    """Idempotent — safe to call from main + tests."""
    for event_type in _VOICE_EVENT_TYPES:
        with contextlib.suppress(ValueError):
            register_handler(event_type, _handle_voice_call_event)


register_voice_handlers()
