"""Outbox event types emitted by the orders module.

Each transition that other modules care about emits one of these. Schemas
are illustrative — the orders module is the producer, so these are just
documentation for consumers.

Inventory subscribes to:
- :data:`EVT_ORDER_PAYMENT_CONFIRMED` → reserve
- :data:`EVT_ORDER_CANCELLED` → release
- :data:`EVT_ORDER_COMPLETED` → consume

Notifications module (when shipped) will subscribe to the rest.
"""

from __future__ import annotations

EVT_ORDER_CREATED = "orders.order.created"
EVT_ORDER_PAYMENT_CONFIRMED = "orders.order.payment_confirmed"
EVT_ORDER_STOCK_RESERVED = "orders.order.stock_reserved"
EVT_ORDER_RESERVATION_FAILED = "orders.order.reservation_failed"
EVT_ORDER_APPROVED = "orders.order.approved"
EVT_ORDER_PACKING_STARTED = "orders.order.packing_started"
EVT_ORDER_DISPATCHED = "orders.order.dispatched"
EVT_ORDER_COMPLETED = "orders.order.completed"
EVT_ORDER_CANCELLED = "orders.order.cancelled"
