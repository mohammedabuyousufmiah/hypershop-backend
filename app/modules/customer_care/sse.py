"""Server-Sent-Events agent inbox stream.

GET /api/inbox/stream  (Bearer JWT) — agent-scoped event channel.

Events published via `publish_inbox_event()` from services after a new message,
handover, or assignment change. Pub/sub backed by Redis when available, falls
back to an in-process asyncio.Queue otherwise (single-pod dev).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, AsyncIterator

from app.config import settings

logger = logging.getLogger(__name__)

_LOCAL: dict[str, list[asyncio.Queue]] = defaultdict(list)


def _redis():
    cfg = settings()
    if not cfg.redis_url:
        return None
    try:
        from redis import Redis
        return Redis.from_url(cfg.redis_url, decode_responses=True)
    except Exception:
        logger.exception("sse_redis_connect_failed")
        return None


def _channel_for(agent_id: str) -> str:
    return f"sse:inbox:{agent_id}"


def publish_inbox_event(agent_id: str | None, event: dict[str, Any]) -> None:
    """Best-effort publish; never raises into request path."""
    if not agent_id:
        agent_id = "_broadcast"
    payload = json.dumps(event, ensure_ascii=False, default=str)
    r = _redis()
    if r is not None:
        try:
            r.publish(_channel_for(agent_id), payload)
            return
        except Exception:
            logger.exception("sse_publish_failed agent=%s", agent_id)
    for q in list(_LOCAL.get(agent_id, [])):
        try:
            q.put_nowait(event)
        except Exception:
            pass


async def stream_for_agent(agent_id: str) -> AsyncIterator[bytes]:
    """Yield SSE-encoded events for the given agent (broadcast also delivered)."""
    r = _redis()

    yield b": connected\n\n"

    if r is not None:
        pubsub = r.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(_channel_for(agent_id), _channel_for("_broadcast"))
        try:
            while True:
                # redis-py pubsub.get_message is SYNC. Wrap in a thread so it
                # doesn't pin the asyncio event loop while waiting up to 15s.
                msg = await asyncio.to_thread(pubsub.get_message, timeout=15.0)
                if msg is None:
                    yield b": ping\n\n"
                    continue
                data = msg.get("data")
                if isinstance(data, (bytes, bytearray)):
                    data = data.decode()
                if data:
                    yield f"data: {data}\n\n".encode()
        except asyncio.CancelledError:  # pragma: no cover
            pass
        finally:
            try:
                pubsub.close()
            except Exception:
                pass
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _LOCAL[agent_id].append(queue)
    _LOCAL["_broadcast"].append(queue)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n".encode()
            except asyncio.TimeoutError:
                yield b": ping\n\n"
    finally:
        for chan in (agent_id, "_broadcast"):
            try:
                _LOCAL[chan].remove(queue)
            except ValueError:
                pass
