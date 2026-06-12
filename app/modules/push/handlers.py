"""Push notification handlers for order, payment, and delivery
lifecycle events.

Subscribes to:

  - orders.order.created             → "Order received" (COD ack)
  - orders.order.payment_confirmed   → "Payment received"
  - orders.order.approved            → "Order approved"
  - orders.order.dispatched          → "Out for delivery"
  - orders.order.completed           → "Order delivered"
  - orders.order.cancelled           → "Order cancelled"
  - payment.failed                   → "Payment didn't go through"
  - payment.cancelled                → "Payment was cancelled"
  - payment.refund.succeeded         → "Refund issued"
  - deliveries.delivery.failed       → "Delivery attempt failed"

Every wired event has ``order_id`` in its payload (verified at audit
time — see ``docs/NOTIFICATIONS_AUDIT.md``). The generic
``_dispatch_for_event`` handler reads it and routes accordingly.

Each handler loads the order, builds a Notification with a
``deep_link`` to the order page, and fans out to all active devices
for the customer. Importing this module registers the handlers as a
side effect (matching the existing pattern in iam/inventory/finance/
invoice_dispatch handlers).
"""

from __future__ import annotations

import contextlib
from uuid import UUID

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.modules.push.service import dispatch_to_user
from app.modules.push.transport.push_base import Notification

_logger = get_logger("hypershop.push.handlers")


# Event type → (title template, body template) — both `.format()`-able
# with order context: order_code, customer_name, currency, grand_total
_PUSH_TEMPLATES: dict[str, tuple[str, str]] = {
    # NEW (turn 35) — fills the COD-no-confirmation-push gap. Orders
    # created via prepaid flow get *both* this AND payment_confirmed
    # within seconds; the duplicate is acceptable (different stages of
    # the funnel deserve different acks).
    "orders.order.created": (
        "Order received",
        "We received order {order_code}. We'll let you know once it's confirmed.",
    ),
    "orders.order.payment_confirmed": (
        "Payment received",
        "We received your payment for order {order_code}. We're getting it ready.",
    ),
    "orders.order.approved": (
        "Order approved",
        "Order {order_code} has been approved and will be packed soon.",
    ),
    "orders.order.dispatched": (
        "Out for delivery",
        "Order {order_code} is on its way. Track it in the app.",
    ),
    "orders.order.completed": (
        "Order delivered",
        "Order {order_code} was delivered. Thanks for shopping with Hypershop!",
    ),
    "orders.order.cancelled": (
        "Order cancelled",
        "Order {order_code} was cancelled. Open the app for details.",
    ),
    # NEW (turn 35) — payment lifecycle: customers were silently left
    # without feedback when a Bkash / SSLCommerz attempt failed or
    # they cancelled at the gateway page. Without a push here, the
    # only signal is the customer noticing their order stayed in
    # PENDING — most don't.
    "payment.failed": (
        "Payment didn't go through",
        "We couldn't capture payment for order {order_code}. Open the app to retry.",
    ),
    "payment.cancelled": (
        "Payment cancelled",
        "Payment for order {order_code} was cancelled. Open the app to try again.",
    ),
    # NEW (turn 36) — closes payment/delivery notification gaps.
    "payment.refund.succeeded": (
        "Refund issued",
        "We've issued a refund for order {order_code}. The amount will reflect in your account in 3–7 business days.",
    ),
    "deliveries.delivery.failed": (
        "Delivery attempt failed",
        "We couldn't complete delivery for order {order_code}. Open the app to reschedule or update your address.",
    ),
}


def _order_id(payload: dict) -> UUID | None:
    raw = payload.get("order_id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


async def _dispatch_for_event(message: OutboxMessage) -> None:
    """Generic handler — looks up the template by event type."""
    template = _PUSH_TEMPLATES.get(message.type)
    if template is None:
        return
    title_tpl, body_tpl = template

    order_id = _order_id(message.payload)
    if order_id is None:
        return

    # Lazy import to avoid circular dependency at module import time.
    from app.modules.iam.models import User
    from app.modules.orders.models import Order

    uow = UnitOfWork()
    async with uow.transactional() as session:
        order = await session.get(Order, order_id)
        if order is None:
            _logger.info("push_skipped_order_missing", order_id=str(order_id))
            return
        user = await session.get(User, order.customer_user_id)
        if user is None:
            return
        ctx = {
            "order_code": order.code,
            "customer_name": user.full_name or "",
            "currency": order.currency,
            "grand_total": str(order.grand_total),
        }
        notif = Notification(
            title=title_tpl.format(**ctx),
            body=body_tpl.format(**ctx),
            data={
                "type": "order_status",
                "order_code": order.code,
                "order_id": str(order.id),
                "status": order.status,
                "deep_link": f"hypershop://orders/{order.code}",
            },
        )
        result = await dispatch_to_user(
            session=session, user_id=user.id, notification=notif,
        )
    _logger.info(
        "push_fanout",
        event=message.type,
        order_code=order.code,
        delivered=result.delivered,
        invalidated=result.invalid_tokens_deactivated,
        transient=result.transient_failures,
        total_devices=result.total_devices,
    )


def register_push_handlers() -> None:
    for ev in _PUSH_TEMPLATES:
        with contextlib.suppress(ValueError):
            register_handler(ev, _dispatch_for_event)


register_push_handlers()
