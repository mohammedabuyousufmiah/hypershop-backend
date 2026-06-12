"""In-process SSE bus for module.config.changed invalidation.

Same pattern as customer_care/sse_bus — single broadcast channel,
asyncio.Queue per subscriber, drop-on-slow-consumer. No agent
targeting needed (every admin tab cares about every config change).

Multi-pod note: drop a Redis pub/sub bridge here when going multi-replica
(mirroring app/modules/customer_care/sse_redis_bridge.py).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.logging import get_logger

_log = get_logger("hypershop.admin_config.sse")

_subscribers: list[asyncio.Queue] = []


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    try:
        _subscribers.remove(q)
    except ValueError:
        pass


def publish(event: dict[str, Any]) -> None:
    """Broadcast to every subscriber. Non-blocking; full queues drop the
    event for that consumer (next config-page mount refreshes anyway).
    """
    payload = json.dumps(event, default=str)
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            _log.warning("module_config_sse_queue_full_dropped",
                         event_type=event.get("type"))


async def event_stream(q: asyncio.Queue):
    """Yield SSE strings; heartbeat every 25s so proxies don't kill the
    connection.
    """
    while True:
        try:
            payload = await asyncio.wait_for(q.get(), timeout=25.0)
            yield f"data: {payload}\n\n"
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
