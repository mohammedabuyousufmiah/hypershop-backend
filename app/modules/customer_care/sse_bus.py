"""In-process Server-Sent-Events bus for the customer-care inbox.

This is an in-process pub/sub — fine for single-replica deployments
and dev. For multi-replica production, swap the asyncio.Queue with a
Redis pub/sub or NATS subject; the public ``publish`` / ``subscribe``
surface stays the same.

Event payload schema (consumed by the agent PWA's ``useInboxEvents``):
- ``{type: "conversation.new", conversation_id, customer_phone, preview}``
- ``{type: "message.received", conversation_id, customer_phone, preview}``
- ``{type: "sla.first_response_breach", conversation_id, due_at}``
- ``{type: "sla.resolution_breach", conversation_id, due_at}``
- ``{type: "handover.requested", conversation_id, reason}``
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any
from uuid import UUID

from app.core.logging import get_logger

_log = get_logger("hypershop.customer_care.sse")

# Each subscriber gets its own queue. Indexed by agent UUID so we can
# target events (e.g. SLA breach notify owner). The special key None
# is the "broadcast" queue every subscriber also listens on.
_subscribers: dict[str | None, list[asyncio.Queue]] = defaultdict(list)


def subscribe(agent_id: UUID | None) -> asyncio.Queue:
    """Register a new subscriber and return their event queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    key = str(agent_id) if agent_id else None
    _subscribers[key].append(q)
    _log.info("sse_subscribe", agent_id=str(agent_id) if agent_id else None)
    return q


def unsubscribe(agent_id: UUID | None, q: asyncio.Queue) -> None:
    key = str(agent_id) if agent_id else None
    try:
        _subscribers[key].remove(q)
    except ValueError:
        pass
    _log.info("sse_unsubscribe", agent_id=str(agent_id) if agent_id else None)


def publish(
    event: dict[str, Any],
    *,
    agent_id: UUID | None = None,
    broadcast: bool = True,
) -> None:
    """Push an event to:
    - the specific agent's queues (if ``agent_id`` provided)
    - the broadcast channel (if ``broadcast=True``)

    Non-blocking — if a subscriber's queue is full (slow consumer) the
    event is dropped for that subscriber and a warning is logged.
    """
    payload = json.dumps(event, default=str)
    # Use a set of queue object-ids to dedupe — a subscriber registered
    # under their agent_id should NOT receive the same event twice when
    # both targeted + broadcast fan-outs apply.
    targets: list[asyncio.Queue] = []
    seen: set[int] = set()
    def _add_from(key: str | None) -> None:
        for q in _subscribers.get(key, []):
            if id(q) in seen:
                continue
            seen.add(id(q))
            targets.append(q)
    if agent_id is not None:
        _add_from(str(agent_id))
    if broadcast:
        # Broadcast fans to ALL registered subscriber queues, regardless
        # of the agent_id they signed up under. This matches the
        # "broadcast channel every subscriber also listens on" intent
        # in the module docstring. Fixed 2026-05-16 — was previously
        # only fanning to subscribers that signed up with agent_id=None.
        for key in list(_subscribers.keys()):
            _add_from(key)
    for q in targets:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            _log.warning("sse_subscriber_queue_full_dropped", event_type=event.get("type"))


async def event_stream(q: asyncio.Queue):
    """Yield SSE-formatted strings from a subscriber queue. Heartbeats
    every 25 seconds so browsers + proxies don't kill the connection.
    """
    while True:
        try:
            payload = await asyncio.wait_for(q.get(), timeout=25.0)
            yield f"data: {payload}\n\n"
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
