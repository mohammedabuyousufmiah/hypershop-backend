"""Outbox handlers owned by the payments module.

Currently only one direction matters:
  - When a payment captures, the orders module needs to know so it can
    transition the order from ``pending_payment`` → ``payment_confirmed``.
    That handler lives in app.modules.orders.handlers (where it already
    listens to other money events) — the payments module just emits the
    event in service.py.

This file exists so the lifespan can `import app.modules.payments.handlers`
as a side-effect (matching the established pattern for iam/inventory/
finance/reminders) — even when there are no current subscriptions, the
slot is reserved for future ones (e.g. reconciliation cron, refund
notification dispatch).
"""

from __future__ import annotations

from app.core.logging import get_logger

_logger = get_logger("hypershop.payments.handlers")
_logger.debug("payments_handlers_loaded")

# Future: register cross-module reactions here. For now intentionally empty —
# orders/finance subscribe to payment.* events from THEIR modules' handlers.
