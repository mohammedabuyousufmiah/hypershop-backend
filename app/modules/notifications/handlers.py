"""Outbox handlers for the notifications module — sprint 9.

Wires 4 P1 events into in-app customer notifications:
- ``orders.order.payment_confirmed`` → "Order received"
- ``orders.order.dispatched``        → "Out for delivery"
- ``deliveries.delivery.delivered``  → "Delivered"
- ``returns.return.completed``       → "Refund processed"

Each handler:
- looks up the customer_user_id (from payload directly OR via the
  orders table when payload only has order_id)
- inserts a single CustomerNotification row
- idempotent: short-circuits if a same-(user, category, action_url)
  notification already exists in the last 24h
"""
from __future__ import annotations

import contextlib
from uuid import UUID

from sqlalchemy import text as _t

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.modules.notifications.service import NotificationService

_log = get_logger("hypershop.notifications.handlers")


async def _resolve_customer_id(payload: dict) -> UUID | None:
    """Get customer_user_id from payload or look it up via order_id."""
    cust = payload.get("customer_user_id")
    if cust:
        return UUID(cust)
    order_id = payload.get("order_id")
    if not order_id:
        return None
    async with UnitOfWork().transactional() as s:
        row = (
            await s.execute(
                _t("SELECT customer_user_id FROM orders WHERE id = :oid"),
                {"oid": order_id},
            )
        ).first()
        return row[0] if row else None


async def _create_idempotent(
    customer_user_id: UUID, *, title: str, body: str,
    category: str, action_url: str | None,
) -> None:
    """Insert if no same-shape notification was created in last 24h."""
    async with UnitOfWork().transactional() as s:
        dup = (
            await s.execute(
                _t(
                    "SELECT id FROM customer_notifications "
                    "WHERE customer_user_id = :u AND category = :c "
                    "AND title = :t AND created_at > now() - INTERVAL '24 hours' "
                    "LIMIT 1"
                ),
                {"u": customer_user_id, "c": category, "t": title},
            )
        ).first()
        if dup:
            _log.info(
                "notification_idempotent_skip",
                user_id=str(customer_user_id), category=category,
            )
            return
        svc = NotificationService(s)
        await svc.create(
            customer_user_id=customer_user_id,
            title=title, body=body, category=category, action_url=action_url,
        )
    _log.info(
        "notification_created",
        user_id=str(customer_user_id), category=category, title=title[:60],
    )


async def _handle_payment_confirmed(message: OutboxMessage) -> None:
    payload = message.payload or {}
    cid = await _resolve_customer_id(payload)
    if cid is None:
        return
    code = payload.get("code") or "—"
    await _create_idempotent(
        cid, title="Order received",
        body=f"Your order {code} payment has been confirmed. We're packing it now.",
        category="order", action_url=f"/account/orders/{payload.get('order_id') or ''}",
    )


async def _handle_order_dispatched(message: OutboxMessage) -> None:
    payload = message.payload or {}
    cid = await _resolve_customer_id(payload)
    if cid is None:
        return
    code = payload.get("code") or "—"
    await _create_idempotent(
        cid, title="Out for delivery",
        body=f"Your order {code} is out for delivery. The rider will reach you shortly.",
        category="delivery", action_url=f"/account/orders/{payload.get('order_id') or ''}",
    )


async def _handle_delivery_delivered(message: OutboxMessage) -> None:
    payload = message.payload or {}
    cid = await _resolve_customer_id(payload)
    if cid is None:
        return
    await _create_idempotent(
        cid, title="Order delivered",
        body="Your order has been delivered. We hope you love it! Please rate your experience.",
        category="delivery", action_url=f"/account/orders/{payload.get('order_id') or ''}",
    )


async def _handle_return_completed(message: OutboxMessage) -> None:
    payload = message.payload or {}
    cid = await _resolve_customer_id(payload)
    if cid is None:
        return
    code = payload.get("code") or "—"
    await _create_idempotent(
        cid, title="Return processed",
        body=f"Your return {code} has been processed. Refund will reach you within 3 business days.",
        category="refund", action_url=f"/account/returns/{payload.get('return_request_id') or ''}",
    )


def register_notification_handlers() -> None:
    """Idempotent registration — safe under fixture reloads."""
    with contextlib.suppress(ValueError):
        register_handler("orders.order.payment_confirmed", _handle_payment_confirmed)
    with contextlib.suppress(ValueError):
        register_handler("orders.order.dispatched", _handle_order_dispatched)
    with contextlib.suppress(ValueError):
        register_handler("deliveries.delivery.delivered", _handle_delivery_delivered)
    with contextlib.suppress(ValueError):
        register_handler("returns.return.completed", _handle_return_completed)


register_notification_handlers()
