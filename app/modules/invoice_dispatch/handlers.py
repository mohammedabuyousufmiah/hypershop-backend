"""Outbox handlers — invoice dispatch on key order events.

Subscribes to:

  - ``payment.captured``
       Customer paid online — emit the invoice as a payment receipt.

Importing this module has the side-effect of registering handlers,
matching the convention in iam/inventory/finance/handlers.py.
"""

from __future__ import annotations

import contextlib
from uuid import UUID

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.modules.invoice_dispatch.service import dispatch_invoice_for_order

_logger = get_logger("hypershop.invoice_dispatch.handlers")


# ---------- event types ----------
EVT_PAYMENT_CAPTURED = "payment.captured"


def _order_id_from_payment_payload(payload: dict) -> UUID | None:
    raw = payload.get("order_id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


async def _handle_payment_captured(message: OutboxMessage) -> None:
    order_id = _order_id_from_payment_payload(message.payload)
    if order_id is None:
        _logger.info(
            "invoice_skipped_no_order_on_payment",
            intent_id=message.payload.get("intent_id"),
        )
        return
    uow = UnitOfWork()
    async with uow.transactional() as session:
        result = await dispatch_invoice_for_order(
            session=session, order_id=order_id,
        )
    _logger.info(
        "invoice_dispatched",
        trigger="payment_captured",
        order_id=str(order_id),
        via=result.via,
        delivered=result.delivered,
        error_code=result.error_code,
    )


def register_invoice_dispatch_handlers() -> None:
    """Idempotent registration."""
    for ev, fn in (
        (EVT_PAYMENT_CAPTURED, _handle_payment_captured),
    ):
        with contextlib.suppress(ValueError):
            register_handler(ev, fn)


register_invoice_dispatch_handlers()
