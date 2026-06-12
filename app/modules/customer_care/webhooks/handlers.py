"""Idempotent ingestion + downstream actions for checkout / payment events."""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    CheckoutEvent,
    Conversation,
    Customer,
    PaymentEvent,
)
from app.observability import WHATSAPP_WEBHOOK
from app.services import enqueue
from app.tenancy import current_tenant_id

logger = logging.getLogger(__name__)


def _safe_decimal(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def ingest_checkout_event(
    db: Session, payload: dict[str, Any], raw_body: bytes | str | None = None
) -> tuple[CheckoutEvent | None, str]:
    """Returns (event_row, status) where status ∈ {'ok','duplicate','invalid'}."""
    provider = (payload.get("provider") or "storefront").strip()[:40]
    event_id = (payload.get("event_id") or payload.get("id") or "").strip()[:120]
    event_type = (payload.get("event_type") or payload.get("status") or "").strip().upper()[:40]

    if not event_id:
        WHATSAPP_WEBHOOK.labels(outcome="checkout_no_event_id").inc()
        return None, "invalid"
    if not event_type:
        WHATSAPP_WEBHOOK.labels(outcome="checkout_no_event_type").inc()
        return None, "invalid"

    row = CheckoutEvent(
        tenant_id=current_tenant_id(),
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        status=payload.get("status"),
        customer_phone=(payload.get("customer_phone") or "")[:40] or None,
        order_id=(payload.get("order_id") or "")[:120] or None,
        raw_payload=(raw_body if isinstance(raw_body, str) else (raw_body.decode("utf-8", "replace") if raw_body else None)),
    )
    try:
        db.add(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        WHATSAPP_WEBHOOK.labels(outcome="checkout_duplicate").inc()
        return None, "duplicate"

    _dispatch_checkout_actions(db, row)
    WHATSAPP_WEBHOOK.labels(outcome="checkout_ok").inc()
    return row, "ok"


def ingest_payment_event(
    db: Session, payload: dict[str, Any], raw_body: bytes | str | None = None
) -> tuple[PaymentEvent | None, str]:
    """Returns (event_row, status) where status ∈ {'ok','duplicate','invalid'}."""
    provider = (payload.get("provider") or "generic").strip()[:40]
    event_id = (payload.get("event_id") or payload.get("id") or "").strip()[:120]
    event_type = (payload.get("event_type") or payload.get("status") or "").strip().upper()[:40]

    if not event_id:
        WHATSAPP_WEBHOOK.labels(outcome="payment_no_event_id").inc()
        return None, "invalid"
    if not event_type:
        WHATSAPP_WEBHOOK.labels(outcome="payment_no_event_type").inc()
        return None, "invalid"

    row = PaymentEvent(
        tenant_id=current_tenant_id(),
        provider=provider,
        event_id=event_id,
        event_type=event_type,
        status=payload.get("status"),
        order_id=(payload.get("order_id") or "")[:120] or None,
        customer_phone=(payload.get("customer_phone") or "")[:40] or None,
        amount=_safe_decimal(payload.get("amount")),
        currency=(payload.get("currency") or "")[:8] or None,
        raw_payload=(raw_body if isinstance(raw_body, str) else (raw_body.decode("utf-8", "replace") if raw_body else None)),
    )
    try:
        db.add(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        WHATSAPP_WEBHOOK.labels(outcome="payment_duplicate").inc()
        return None, "duplicate"

    _dispatch_payment_actions(db, row)
    WHATSAPP_WEBHOOK.labels(outcome="payment_ok").inc()
    return row, "ok"


# ───── downstream actions ────────────────────────────────────────────


def _dispatch_checkout_actions(db: Session, event: CheckoutEvent) -> None:
    """Idempotent next-action enqueue based on event_type. Failures are
    logged but never raise — they would otherwise undo the idempotent
    insert on retry."""
    try:
        et = (event.event_type or "").upper()
        if et == "ORDER_CONFIRMED" and event.customer_phone:
            enqueue("whatsapp-send-queue", {
                "to": event.customer_phone,
                "text": _bn_or_en(
                    db, event.customer_phone,
                    en=f"Thank you! Your order {event.order_id or ''} is confirmed. We'll deliver soon.",
                    bn=f"ধন্যবাদ! আপনার অর্ডার {event.order_id or ''} নিশ্চিত হয়েছে। শীঘ্রই ডেলিভারি দেওয়া হবে।",
                ),
            })
        elif et == "DELIVERY_PENDING" and event.customer_phone:
            enqueue("whatsapp-send-queue", {
                "to": event.customer_phone,
                "text": _bn_or_en(
                    db, event.customer_phone,
                    en=f"Your order {event.order_id or ''} is out for delivery.",
                    bn=f"আপনার অর্ডার {event.order_id or ''} ডেলিভারির পথে।",
                ),
            })
        elif et == "DELIVERY_COMPLETED" and event.customer_phone:
            convo = _conversation_for(db, event.customer_phone)
            if convo:
                enqueue("csat-send-queue", {"conversation_id": convo.id})
        event.processed = True
        event.processed_at = datetime.utcnow()
        db.commit()
    except Exception:
        logger.exception("checkout_action_dispatch_failed event_id=%s", event.event_id)


def _dispatch_payment_actions(db: Session, event: PaymentEvent) -> None:
    try:
        et = (event.event_type or "").upper()
        if et == "PAYMENT_SUCCESS" and event.customer_phone:
            amount_str = (
                f"{event.amount} {event.currency or ''}".strip()
                if event.amount is not None
                else "your payment"
            )
            enqueue("whatsapp-send-queue", {
                "to": event.customer_phone,
                "text": _bn_or_en(
                    db, event.customer_phone,
                    en=f"Payment received: {amount_str}. Thank you!",
                    bn=f"পেমেন্ট পাওয়া গেছে: {amount_str}। ধন্যবাদ!",
                ),
            })
        elif et == "PAYMENT_FAILED" and event.customer_phone:
            enqueue("whatsapp-send-queue", {
                "to": event.customer_phone,
                "text": _bn_or_en(
                    db, event.customer_phone,
                    en="Payment failed. Please try again or contact support.",
                    bn="পেমেন্ট ব্যর্থ হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন বা সাপোর্টে যোগাযোগ করুন।",
                ),
            })
        elif et in ("PAYMENT_REFUNDED", "REFUNDED") and event.customer_phone:
            enqueue("whatsapp-send-queue", {
                "to": event.customer_phone,
                "text": _bn_or_en(
                    db, event.customer_phone,
                    en=f"Refund processed for order {event.order_id or ''}.",
                    bn=f"অর্ডার {event.order_id or ''}-এর রিফান্ড প্রক্রিয়া করা হয়েছে।",
                ),
            })
        event.processed = True
        event.processed_at = datetime.utcnow()
        db.commit()
    except Exception:
        logger.exception("payment_action_dispatch_failed event_id=%s", event.event_id)


def _conversation_for(db: Session, phone: str) -> Conversation | None:
    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if not customer:
        return None
    return db.scalar(
        select(Conversation)
        .where(Conversation.customer_id == customer.id)
        .order_by(Conversation.last_message_at.desc())
        .limit(1)
    )


def _bn_or_en(db: Session, phone: str, *, en: str, bn: str) -> str:
    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if customer and (customer.preferred_language or "").lower() == "english":
        return en
    return bn
