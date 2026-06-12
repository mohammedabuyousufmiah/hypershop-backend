"""Redis pub/sub bridge for the module-config in-process SSE bus.

Mirror of ``app/modules/customer_care/sse_redis_bridge.py`` — same
single-channel + self-loop-suppression pattern.

In single-pod dev mode the in-process ``sse_bus`` is enough — the
endpoint that handles the PUT also calls ``sse_bus.publish`` directly.
In multi-pod prod, a flip on pod A must reach SSE subscribers attached
to pod B; this bridge does the cross-pod fan-out via a single
broadcast Redis channel:

    hypershop:module_config_events

Each pod tags its own publishes with ``_origin_pod_id`` so the
listener loop drops them — preventing the originating pod from
double-emitting to its own local subscribers (which already received
the event via the in-process publish in ``_emit_changed``).

If REDIS_URL is unset / unreachable the bridge silently no-ops —
single-pod dev keeps working.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.modules.admin_config import sse_bus

_log = get_logger("hypershop.admin_config.sse_redis_bridge")

CHANNEL = "hypershop:module_config_events"
POD_ID = str(uuid.uuid4())

_listener_task: asyncio.Task | None = None


def publish_config_event(event: dict[str, Any]) -> asyncio.Task | None:
    """Fire-and-forget Redis publish. Tagged with this pod's id so the
    listener loop on this pod skips it.

    No-ops if no running event loop is available (caller should always
    be inside one — request/handler/lifespan context).
    """
    enriched = {**event, "_origin_pod_id": POD_ID}
    payload = json.dumps(enriched, default=str)

    async def _publish() -> None:
        try:
            r = get_redis()
            await r.publish(CHANNEL, payload)
        except Exception as e:  # noqa: BLE001
            _log.warning("module_config_redis_publish_failed", error=str(e))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(_publish())


async def _listen_forever() -> None:
    """Subscribe to CHANNEL and re-emit foreign events onto local sse_bus.
    Exponential reconnect backoff on connection drop.
    """
    backoff = 1.0
    while True:
        try:
            r = get_redis()
            pubsub = r.pubsub()
            await pubsub.subscribe(CHANNEL)
            _log.info("module_config_redis_listener_started",
                      pod_id=POD_ID, channel=CHANNEL)
            backoff = 1.0
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
                msg.pop("_origin_pod_id", None)
                sse_bus.publish(msg)
                _log.info(
                    "module_config_redis_consumed",
                    type=msg.get("type"),
                    module_key=msg.get("module_key"),
                    key=msg.get("key"),
                )
        except asyncio.CancelledError:
            _log.info("module_config_redis_listener_cancelled", pod_id=POD_ID)
            return
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "module_config_redis_listener_disconnect",
                pod_id=POD_ID, error=str(e), backoff_seconds=backoff,
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, 30.0)


def start_listener() -> None:
    """Idempotent — second call while task is still running is a no-op."""
    global _listener_task
    if _listener_task is not None and not _listener_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log.warning("module_config_redis_listener_no_loop")
        return
    _listener_task = loop.create_task(_listen_forever())


async def stop_listener() -> None:
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _listener_task = None
