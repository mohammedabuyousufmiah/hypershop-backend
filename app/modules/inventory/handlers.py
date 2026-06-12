"""Outbox handlers that drive inventory state changes from cross-module events.

Importing this module registers the handlers as a side effect (mirrors the
pattern in ``app.modules.iam.handlers``). The dispatcher invokes the right
handler by ``OutboxMessage.type``.

Each handler:

1. Validates the payload via its Pydantic contract — payload bugs surface
   as a typed ``ValidationError`` and roll the message back to retry,
   eventually dead-lettering.
2. Opens its own ``UnitOfWork.transactional()`` scope. The dispatcher does
   not own the handler's transaction, so we manage our own boundary; that
   way the audit + ledger + outbox writes commit atomically and a handler
   failure rolls back the whole effect, leaving the message available for
   retry without partial state.
3. Calls into :class:`OrderStockOrchestrator`, which is already idempotent
   on ``order_id``. So at-least-once redelivery from the dispatcher is safe:
   re-running the handler on the same payload is a no-op, not a double-book.
"""

from __future__ import annotations

import contextlib

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.core.security.principal import SystemPrincipal
from app.modules.inventory.events import (
    EVT_ORDER_CANCELLED,
    EVT_ORDER_COMPLETED,
    EVT_PAYMENT_SUCCEEDED,
    OrderCancelledPayload,
    OrderCompletedPayload,
    PaymentSucceededPayload,
)
from app.modules.inventory.orchestrator import OrderItem, OrderStockOrchestrator

_logger = get_logger("hypershop.inventory.handlers")


async def _handle_payment_succeeded(message: OutboxMessage) -> None:
    payload = PaymentSucceededPayload.model_validate(message.payload)
    items = [
        OrderItem(variant_id=item.variant_id, quantity=item.quantity)
        for item in payload.items
    ]
    uow = UnitOfWork()
    async with uow.transactional() as session:
        result = await OrderStockOrchestrator(session).reserve_for_order(
            principal=SystemPrincipal(),
            order_id=payload.order_id,
            items=items,
            warehouse_code=payload.warehouse_code,
        )
    _logger.info(
        "inventory_reserve_for_order",
        order_id=str(payload.order_id),
        already_reserved=result.already_reserved,
        item_count=len(items),
    )


async def _handle_order_cancelled(message: OutboxMessage) -> None:
    payload = OrderCancelledPayload.model_validate(message.payload)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        released = await OrderStockOrchestrator(session).release_for_order(
            principal=SystemPrincipal(),
            order_id=payload.order_id,
            notes=payload.reason,
        )
    _logger.info(
        "inventory_release_for_order",
        order_id=str(payload.order_id),
        released=released,
    )


async def _handle_order_completed(message: OutboxMessage) -> None:
    payload = OrderCompletedPayload.model_validate(message.payload)
    uow = UnitOfWork()
    async with uow.transactional() as session:
        consumed = await OrderStockOrchestrator(session).consume_for_order(
            principal=SystemPrincipal(),
            order_id=payload.order_id,
        )
    _logger.info(
        "inventory_consume_for_order",
        order_id=str(payload.order_id),
        consumed=consumed,
    )


def register_inventory_handlers() -> None:
    """Idempotent registration — safe to call multiple times (tests do)."""
    for event_type, handler in (
        (EVT_PAYMENT_SUCCEEDED, _handle_payment_succeeded),
        (EVT_ORDER_CANCELLED, _handle_order_cancelled),
        (EVT_ORDER_COMPLETED, _handle_order_completed),
    ):
        with contextlib.suppress(ValueError):
            register_handler(event_type, handler)


register_inventory_handlers()
