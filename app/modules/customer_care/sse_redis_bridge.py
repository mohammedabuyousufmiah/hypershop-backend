"""Redis pub/sub bridge for the in-process SSE bus.

In single-pod dev mode ``sse_bus`` (asyncio.Queue per subscriber) is
enough — producer + subscriber share memory. In multi-pod prod a call
ringing on pod A but with the agent's softphone SSE attached to pod B
silently fails: B's local sse_bus never sees the event.

This bridge fixes that by mirroring every voice-call event onto a
single Redis pub/sub channel:

    hypershop:voice_call_events

Producers (``voice_handlers._handle_voice_call_event``) publish to BOTH
the local sse_bus (zero-latency for same-pod subscribers) AND to the
Redis channel via :func:`publish_voice_event`. Each pod runs a
background listener task (``start_listener``) that consumes the channel
and re-emits onto its own local sse_bus — fanning the event to
subscribers attached to THAT pod.

Anti-loop: each pod tags its publishes with ``_origin_pod_id``. The
listener skips messages whose origin matches its own pod id so the
producer pod doesn't double-emit.

If ``REDIS_URL`` is unset / unreachable the bridge silently no-ops —
single-pod mode keeps working.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.modules.customer_care import sse_bus

_log = get_logger("hypershop.customer_care.sse_redis_bridge")

CHANNEL = "hypershop:voice_call_events"

# Per-process identifier so a pod's own publishes can be filtered out
# of its consumer loop (avoids double-emit on the originating pod).
POD_ID = str(uuid.uuid4())

_listener_task: asyncio.Task | None = None


def publish_voice_event(event: dict[str, Any]) -> asyncio.Task | None:
    """Fire-and-forget Redis publish. Returns the asyncio.Task so callers
    can opt to await it, but the typical pattern is to let it run.

    No-ops if Redis isn't reachable (caught + logged at WARNING). Tagged
    with this pod's id so our own listener loop can skip it.
    """
    enriched = {**event, "_origin_pod_id": POD_ID}
    payload = json.dumps(enriched, default=str)

    async def _publish() -> None:
        try:
            r = get_redis()
            await r.publish(CHANNEL, payload)
        except Exception as e:  # noqa: BLE001
            _log.warning("voice_event_redis_publish_failed", error=str(e))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Caller isn't in an event loop — Redis publish gets dropped.
        # This shouldn't happen in normal request/handler context.
        return None
    return loop.create_task(_publish())


async def _listen_forever() -> None:
    """Subscribe to CHANNEL and re-emit each foreign event to local sse_bus.

    Reconnects with exponential backoff on connection drop. Skips
    messages tagged with our own POD_ID to prevent the producer pod from
    double-emitting.
    """
    backoff = 1.0
    while True:
        try:
            r = get_redis()
            pubsub = r.pubsub()
            await pubsub.subscribe(CHANNEL)
            _log.info("voice_event_redis_listener_started", pod_id=POD_ID, channel=CHANNEL)
            backoff = 1.0  # reset on successful connect
            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                try:
                    msg = json.loads(raw.get("data") or "{}")
                except Exception:  # noqa: BLE001
                    continue
                if msg.get("_origin_pod_id") == POD_ID:
                    # Same pod — local sse_bus already received this.
                    continue
                # Strip the origin marker before fanning locally.
                msg.pop("_origin_pod_id", None)
                agent_id_str = msg.get("agent_id")
                agent_id_uuid = None
                if agent_id_str:
                    try:
                        agent_id_uuid = uuid.UUID(str(agent_id_str))
                    except (ValueError, TypeError):
                        agent_id_uuid = None
                sse_bus.publish(msg, agent_id=agent_id_uuid, broadcast=True)
                _log.info(
                    "voice_event_redis_consumed",
                    type=msg.get("type"),
                    voice_call_id=msg.get("voice_call_id"),
                    agent_id=str(agent_id_uuid) if agent_id_uuid else None,
                )
        except asyncio.CancelledError:
            _log.info("voice_event_redis_listener_cancelled", pod_id=POD_ID)
            return
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "voice_event_redis_listener_disconnect",
                pod_id=POD_ID, error=str(e), backoff_seconds=backoff,
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, 30.0)


def start_listener() -> None:
    """Start the background pubsub listener for this pod. Idempotent —
    a second call while the first task is still running is a no-op.

    Call once from FastAPI's lifespan or app startup. The task runs for
    the lifetime of the process; it's stopped only on shutdown via
    :func:`stop_listener`.
    """
    global _listener_task
    if _listener_task is not None and not _listener_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log.warning("voice_event_redis_listener_no_loop")
        return
    _listener_task = loop.create_task(_listen_forever())


async def stop_listener() -> None:
    """Cancel the background pubsub listener cleanly."""
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _listener_task = None
