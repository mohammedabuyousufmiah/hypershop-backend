from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.events.types import DomainEvent
from app.core.logging import get_logger

E = TypeVar("E", bound=DomainEvent)
Handler = Callable[[E], Awaitable[None]]

_logger = get_logger("hypershop.events")


class EventBus:
    """Tiny in-process pub/sub for synchronous side effects.

    For cross-process or durable side effects use the outbox table —
    in-process handlers run in the publisher's transaction and crash takes
    them down with it.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        handlers = self._subscribers.get(event.type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                _logger.exception(
                    "event_handler_failed",
                    event_type=event.type,
                    event_id=str(event.event_id),
                )
                raise


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
