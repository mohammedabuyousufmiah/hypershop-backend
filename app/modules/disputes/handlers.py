"""Outbox handlers for the disputes module.

When dispute state transitions fire via `enqueue_outbox`, this module
picks them up + dispatches push (and later email/WhatsApp) to the
right party (buyer or seller depending on event).

Soft-fails: handler logs + returns on any error so the dispatcher
doesn't dead-letter the message permanently. Push delivery itself
already falls through to log_only when FCM/APNs creds are missing.
"""
from __future__ import annotations

import contextlib
from typing import Literal
from uuid import UUID

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.outbox import OutboxMessage
from app.core.logging import get_logger
from app.modules.push.service import Notification, dispatch_to_user

_logger = get_logger("hypershop.disputes.handlers")

# Event type → (recipient_role, title, body_template).
# `body_template` uses str.format() with `{subject}`, `{dispute_id}`,
# `{seller_name}`, `{customer_name}` keys filled from the payload.
_RECIPIENT = Literal["buyer", "seller"]

_DISPUTE_TEMPLATES: dict[str, tuple[_RECIPIENT, str, str]] = {
    # Buyer opens → notify seller.
    "disputes.dispute.opened": (
        "seller",
        "New dispute filed",
        "A buyer opened a dispute on your order: {subject}. Respond within 48h or the case escalates to a mediator.",
    ),
    # Seller posts a reply → notify buyer.
    "disputes.dispute.seller_responded": (
        "buyer",
        "Seller responded to your dispute",
        "The seller replied on '{subject}'. Open the app to read + reply.",
    ),
    # Buyer posts a reply → notify seller.
    "disputes.dispute.buyer_responded": (
        "seller",
        "Buyer replied on the dispute",
        "The buyer replied on '{subject}'. Respond to keep the case moving.",
    ),
    # 48h SLA breach → notify seller (last warning) + buyer (transparency).
    "disputes.dispute.escalated": (
        "seller",
        "Dispute escalated to mediator",
        "Your response window on '{subject}' lapsed. A mediator has taken the case.",
    ),
    # Mediator decision → notify buyer (refund expectation) + seller (debit).
    "disputes.dispute.resolved": (
        "buyer",
        "Dispute resolved",
        "Decision on '{subject}'. Open the app for the resolution details.",
    ),
    # Manual close after resolution → notify both (we pick buyer; seller
    # already saw the resolution event).
    "disputes.dispute.closed": (
        "buyer",
        "Dispute closed",
        "Case '{subject}' has been closed.",
    ),
}


def _uuid(raw: object) -> UUID | None:
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def _dispatch_for_event(message: OutboxMessage) -> None:
    template = _DISPUTE_TEMPLATES.get(message.type)
    if template is None:
        return
    recipient_role, title_tpl, body_tpl = template
    payload = dict(message.payload or {})

    # Resolve recipient user_id by role. Payload MUST include both
    # `customer_user_id` (buyer) and `seller_user_id` (seller's user
    # — NOT seller_id which is the sellers.id row).
    if recipient_role == "buyer":
        recipient_id = _uuid(payload.get("customer_user_id"))
    else:
        recipient_id = _uuid(payload.get("seller_user_id"))

    if recipient_id is None:
        _logger.info(
            "dispute_push_skipped_no_recipient",
            event=message.type,
            dispute_id=str(payload.get("dispute_id") or ""),
        )
        return

    subject = str(payload.get("subject") or "your order issue")[:120]
    notif = Notification(
        title=title_tpl,
        body=body_tpl.format(subject=subject),
        data={
            "type": "dispute_status",
            "event": message.type,
            "dispute_id": str(payload.get("dispute_id") or ""),
            "order_id": str(payload.get("order_id") or ""),
            "deep_link": (
                "hypershop://disputes/" + str(payload.get("dispute_id") or "")
            ),
        },
    )

    try:
        uow = UnitOfWork()
        async with uow.transactional() as session:
            result = await dispatch_to_user(
                session=session, user_id=recipient_id, notification=notif,
            )
    except Exception as e:  # noqa: BLE001
        _logger.warning(
            "dispute_push_dispatch_failed",
            event=message.type,
            dispute_id=str(payload.get("dispute_id") or ""),
            error=str(e)[:200],
        )
        return

    _logger.info(
        "dispute_push_fanout",
        event=message.type,
        dispute_id=str(payload.get("dispute_id") or ""),
        recipient_role=recipient_role,
        delivered=result.delivered,
        invalidated=result.invalid_tokens_deactivated,
        transient=result.transient_failures,
        total_devices=result.total_devices,
    )


def register_dispute_handlers() -> None:
    for ev in _DISPUTE_TEMPLATES:
        with contextlib.suppress(ValueError):
            register_handler(ev, _dispatch_for_event)


# Auto-register at module import time so callers only need
# `from app.modules.disputes import handlers as _disp_handlers`
# to wire all 6 events. Mirrors the push.handlers pattern.
register_dispute_handlers()
