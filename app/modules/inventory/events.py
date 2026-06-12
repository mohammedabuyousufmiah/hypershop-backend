"""Outbox event contracts the inventory module subscribes to.

These constants and schemas are the public contract for any module that
needs to drive a stock movement via an outbox event. Producers (orders,
payments, deliveries) MUST emit messages with ``type`` matching one of the
constants below and ``payload`` validating against the matching Pydantic
schema. Mismatched payloads are caught by the handler and surfaced as
retry → dead-letter rather than silently dropping the movement.

Why this lives in inventory and not at a shared layer
-----------------------------------------------------
The inventory module owns the *interpretation* of these events. Other
modules just emit; they don't need to know about ledger semantics. This
keeps the dependency direction clean: orders/payments do not import
anything from inventory beyond optionally constructing the event-type
string.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------- Event type names ----------------

# Emitted by payments module when a payment for an order finalises in the
# customer's favour (authorised + captured, or in instant-capture flows: paid).
# Triggers FEFO reservation. Idempotent on order_id.
EVT_PAYMENT_SUCCEEDED = "payments.payment.succeeded"

# Emitted by orders module when an order is cancelled or fully refunded.
# Triggers release of any reservation booked under the order_id.
EVT_ORDER_CANCELLED = "orders.order.cancelled"

# Emitted by the orders module on the COMPLETED transition (= delivered to
# customer). Triggers consume — drains reserved units out of the system.
EVT_ORDER_COMPLETED = "orders.order.completed"


# ---------------- Payload schemas ----------------


class _ConsumerPayload(BaseModel):
    """Base for consumer-side payload schemas.

    Producers carry richer data than any single consumer needs (e.g. order
    code, status transition info, customer ids for notifications). We
    ``extra="ignore"`` on the consumer side so adding a field upstream
    doesn't break us.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class OrderItemPayload(_ConsumerPayload):
    variant_id: UUID
    quantity: int = Field(..., ge=1, le=10_000_000)


class PaymentSucceededPayload(_ConsumerPayload):
    """Payload for ``payments.payment.succeeded``.

    The orders module is the source of truth for which lines belong to which
    order; the payments module pulls them and inlines them here so the
    inventory handler can do its work without a cross-module fetch.
    """

    order_id: UUID
    items: list[OrderItemPayload] = Field(..., min_length=1, max_length=500)
    warehouse_code: str | None = Field(default=None, min_length=1, max_length=32)


class OrderCancelledPayload(_ConsumerPayload):
    order_id: UUID
    reason: str | None = Field(default=None, max_length=512)


class OrderCompletedPayload(_ConsumerPayload):
    order_id: UUID
    completed_at: str | None = None  # ISO-8601; informational only


__all__ = [
    "EVT_ORDER_CANCELLED",
    "EVT_ORDER_COMPLETED",
    "EVT_PAYMENT_SUCCEEDED",
    "OrderCancelledPayload",
    "OrderCompletedPayload",
    "OrderItemPayload",
    "PaymentSucceededPayload",
]
