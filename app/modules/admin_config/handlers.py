"""Outbox handler: module.config.changed → in-process SSE bus.

Same pattern as customer_care/voice_handlers — registers a handler with
``app.core.events.dispatcher`` that forwards the outbox payload onto
``sse_bus.publish`` so subscribers to
``GET /admin/modules/_stream`` see config changes in real time.

Importing this module is the side effect that registers the handler.
"""
from __future__ import annotations

import contextlib

from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.modules.admin_config import sse_bus
from app.modules.admin_config.api.settings import EVT_CONFIG_CHANGED

_log = get_logger("hypershop.admin_config.handlers")


async def _handle_config_changed(message: OutboxMessage) -> None:
    payload = dict(message.payload or {})
    event = {"type": message.type, **payload}
    sse_bus.publish(event)
    _log.info(
        "module_config_changed_forwarded",
        module_key=payload.get("module_key"),
        kind=payload.get("kind"),
        key=payload.get("key"),
        op=payload.get("op"),
    )


def register_admin_config_handlers() -> None:
    with contextlib.suppress(ValueError):
        register_handler(EVT_CONFIG_CHANGED, _handle_config_changed)


register_admin_config_handlers()
