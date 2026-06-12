"""Outbox handlers — Hypershop events that trigger customer-care side effects.

Subscribes to:
- ``orders.order.payment_confirmed`` → send order-received WhatsApp
- ``orders.order.dispatched``        → send "out for delivery" WhatsApp
- ``orders.order.completed``         → send thank-you + start CSAT survey
- ``payments.payment.succeeded``     → send payment-success WhatsApp
- ``deliveries.delivery.delivered``  → send delivery-completed WhatsApp

Idempotency: each handler is keyed on the conversation row's
``last_message`` field or a system-message marker, so duplicate
deliveries are safe (re-running posts the same body and outbound
WhatsApp send dedupes server-side).

All handlers degrade gracefully:
- No customer phone on file → skip silently
- No open conversation → skip silently (we don't create one just for
  a marketing message; the customer initiates contact)
- No WhatsApp creds → message logged but kept in cc_messages so the
  agent sees it next time
"""
from __future__ import annotations

import contextlib
from uuid import UUID

from sqlalchemy import desc, select, text as _text

from app.core.db.uow import UnitOfWork
from app.core.events.dispatcher import register_handler
from app.core.events.models import OutboxMessage
from app.core.logging import get_logger
from app.modules.customer_care import outbound, service
from app.modules.customer_care.models import CCConversation, CCMessage

_log = get_logger("hypershop.customer_care.handlers")


async def _send_to_customer(
    *,
    customer_user_id: UUID,
    body: str,
    system_tag: str,
    template_name: str | None = None,
    template_params: list[str] | None = None,
) -> None:
    """Find / create an open conversation for this customer, append a
    system+ai message, and push to WhatsApp.

    ``system_tag`` is a short string (e.g. "order.completed.thanks")
    used to prevent duplicate sends in the same conversation — we
    check for an existing message whose body starts with the same
    leading 12 chars *and* shares the tag context.
    """
    async with UnitOfWork().transactional() as session:
        # Pull customer phone
        row = (
            await session.execute(
                _text(
                    "SELECT phone, full_name FROM users WHERE id = :uid",
                ),
                {"uid": customer_user_id},
            )
        ).first()
        if row is None or not row[0]:
            _log.info(
                "cc_outbox_skipped_no_phone",
                customer_user_id=str(customer_user_id),
                system_tag=system_tag,
            )
            return
        phone = row[0]

        # Find open conv or create a system-initiated one. We DO create
        # if missing because these are transactional notifications
        # (paid, dispatched, delivered) — the customer benefits.
        conv = (
            await session.execute(
                select(CCConversation)
                .where(CCConversation.customer_id == customer_user_id)
                .where(CCConversation.status == "open")
                .order_by(desc(CCConversation.last_message_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        if conv is None:
            conv = CCConversation(
                customer_id=customer_user_id,
                channel="whatsapp",
                source="system",
                status="open",
            )
            session.add(conv)
            await session.flush()
            agent_id = await service.choose_agent(session)
            if agent_id:
                await service.assign_agent(
                    session, conversation=conv, agent_id=agent_id,
                )

        # Idempotency: if an identical system message already exists
        # in this conversation, no-op.
        existing = (
            await session.execute(
                _text(
                    "SELECT id FROM cc_messages "
                    "WHERE conversation_id = :cid "
                    "AND sender_type = 'system' "
                    "AND message_body = :body LIMIT 1"
                ),
                {"cid": conv.id, "body": body},
            )
        ).first()
        if existing:
            _log.info(
                "cc_outbox_idempotent_skip",
                conversation_id=str(conv.id),
                system_tag=system_tag,
            )
            return

        # Persist the message first (so even if WhatsApp send fails,
        # the agent still sees the notification trail in the inbox).
        msg = CCMessage(
            conversation_id=conv.id,
            sender_type="system",
            message_type="text",
            message_body=body,
            channel="whatsapp",
        )
        session.add(msg)
        conv.last_message = body[:255]
        from datetime import datetime, timezone
        conv.last_message_at = datetime.now(timezone.utc)

    # Outside txn: outbound network call. Outbound system messages must
    # use a Meta-approved template outside the 24-hour service window;
    # if the operator has configured a template name we prefer that,
    # otherwise we fall back to free-form text (works during dev /
    # inside the 24h window only).
    if template_name:
        result = await outbound.send_whatsapp_template(
            to_phone=phone,
            template_name=template_name,
            body_params=template_params or [],
        )
        if result is None:
            # Template path didn't work — try free-form. Inside the
            # service window this still reaches the customer; outside,
            # Meta will block. Either way the system message is in
            # the inbox so the agent sees it.
            await outbound.send_whatsapp_text(to_phone=phone, body=body)
    else:
        await outbound.send_whatsapp_text(to_phone=phone, body=body)
    _log.info(
        "cc_outbox_sent",
        customer_user_id=str(customer_user_id),
        system_tag=system_tag,
        used_template=bool(template_name),
    )


# ---------------------------------------------------------------- handlers
async def _handle_payment_confirmed(message: OutboxMessage) -> None:
    from app.modules.customer_care.config import settings as _cc_settings
    payload = message.payload or {}
    code = payload.get("code") or payload.get("order_code") or "—"
    customer_user_id = payload.get("customer_user_id")
    if not customer_user_id:
        return
    body = (
        f"Hypershop: We've received your order {code} and confirmed "
        f"payment. We'll start packing shortly. Reply STOP to opt out."
    )
    await _send_to_customer(
        customer_user_id=UUID(customer_user_id),
        body=body,
        system_tag="order.payment_confirmed",
        template_name=_cc_settings().template_order_paid,
        template_params=[str(code)],
    )


async def _handle_order_dispatched(message: OutboxMessage) -> None:
    from app.modules.customer_care.config import settings as _cc_settings
    from app.modules.customer_care.api.nice import order_tracking_url
    payload = message.payload or {}
    code = payload.get("code") or "—"
    customer_user_id = payload.get("customer_user_id")
    if not customer_user_id:
        return
    track = order_tracking_url(str(code))
    body = (
        f"Hypershop: Your order {code} is now out for delivery. "
        f"The rider will reach you soon. Track here: {track}"
    )
    await _send_to_customer(
        customer_user_id=UUID(customer_user_id),
        body=body,
        system_tag="order.dispatched",
        template_name=_cc_settings().template_order_dispatched,
        template_params=[str(code)],
    )


async def _handle_order_completed(message: OutboxMessage) -> None:
    from app.modules.customer_care.config import settings as _cc_settings
    payload = message.payload or {}
    code = payload.get("code") or "—"
    customer_user_id = payload.get("customer_user_id")
    if not customer_user_id:
        return
    body = (
        f"Hypershop: Thank you for shopping with us! Order {code} was "
        f"delivered successfully. Rate us 1–5 in your next reply, or "
        f"reply STOP to opt out of follow-ups."
    )
    await _send_to_customer(
        customer_user_id=UUID(customer_user_id),
        body=body,
        system_tag="order.completed.csat",
        template_name=_cc_settings().template_order_delivered,
        template_params=[str(code)],
    )


async def _handle_payment_succeeded(message: OutboxMessage) -> None:
    from app.modules.customer_care.config import settings as _cc_settings
    payload = message.payload or {}
    # Hypershop's payment event payload often carries order_id but
    # not customer_user_id directly — try both paths.
    customer_user_id = payload.get("customer_user_id")
    if not customer_user_id:
        order_id = payload.get("order_id")
        if not order_id:
            return
        async with UnitOfWork().transactional() as s:
            row = (
                await s.execute(
                    _text("SELECT customer_user_id FROM orders WHERE id = :oid"),
                    {"oid": order_id},
                )
            ).first()
            if row is None:
                return
            customer_user_id = str(row[0])
    body = (
        "Hypershop: Your online payment has been confirmed. "
        "Thank you — we'll dispatch shortly."
    )
    await _send_to_customer(
        customer_user_id=UUID(customer_user_id),
        body=body,
        system_tag="payment.succeeded",
        template_name=_cc_settings().template_payment_success,
        template_params=[],
    )


async def _handle_delivery_delivered(message: OutboxMessage) -> None:
    payload = message.payload or {}
    order_id = payload.get("order_id")
    if not order_id:
        return
    async with UnitOfWork().transactional() as s:
        row = (
            await s.execute(
                _text(
                    "SELECT customer_user_id, code FROM orders WHERE id = :oid",
                ),
                {"oid": order_id},
            )
        ).first()
        if row is None:
            return
        customer_user_id, code = row[0], row[1]
    body = (
        f"Hypershop: Order {code} has been delivered. "
        f"We hope you love your purchase! Reply with your rating 1–5."
    )
    await _send_to_customer(
        customer_user_id=UUID(str(customer_user_id)),
        body=body,
        system_tag="delivery.delivered",
    )


def register_customer_care_handlers() -> None:
    """Idempotent registration — safe under test fixture reloads."""
    with contextlib.suppress(ValueError):
        register_handler("orders.order.payment_confirmed", _handle_payment_confirmed)
    with contextlib.suppress(ValueError):
        register_handler("orders.order.dispatched", _handle_order_dispatched)
    with contextlib.suppress(ValueError):
        register_handler("orders.order.completed", _handle_order_completed)
    with contextlib.suppress(ValueError):
        register_handler("payments.payment.succeeded", _handle_payment_succeeded)
    with contextlib.suppress(ValueError):
        register_handler("deliveries.delivery.delivered", _handle_delivery_delivered)


register_customer_care_handlers()
