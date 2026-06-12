"""Business logic: queueing, agent routing, AI reply, message ingestion."""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.channels import IncomingMessage
from app.config import settings
from app.models import (
    Conversation,
    Customer,
    DEFAULT_TENANT_ID,
    Followup,  # noqa: F401  (re-exported for compat)
    Message,
    Order,  # noqa: F401  (re-exported for compat)
    Product,
    User,
    WebhookIdempotency,
)
from app.observability import WHATSAPP_WEBHOOK
from app.sla import apply_sla_to_conversation
from app.sse import publish_inbox_event

logger = logging.getLogger(__name__)

QUEUE_NAMES = [
    "incoming-message-queue",
    "ai-reply-queue",
    "human-handover-queue",
    "whatsapp-send-queue",
    "google-sheet-sync-queue",
    "followup-message-queue",
    "order-confirmation-queue",
    "agent-reassignment-queue",
    "audit-log-queue",
    "csat-send-queue",
    "sla-scan-queue",
    "voice-stt-queue",
    "voice-tts-queue",
]
QUEUES: dict[str, deque] = defaultdict(deque)

HANDOVER_BANGLA = (
    "আমি আপনার বিষয়টি আমাদের কাস্টমার কেয়ার টিমে পাঠাচ্ছি। "
    "একজন প্রতিনিধি দ্রুত আপনাকে সাহায্য করবেন।"
)
HANDOVER_ENGLISH = (
    "I'm forwarding this to our customer care team. "
    "A representative will assist you shortly."
)


def enqueue(name: str, payload: dict) -> dict:
    if name not in QUEUE_NAMES:
        raise ValueError(f"Unknown queue {name}")
    cfg = settings()
    if cfg.redis_url:
        try:
            from redis import Redis
            from rq import Queue

            queue = Queue(name, connection=Redis.from_url(cfg.redis_url), default_timeout=300)
            job = queue.enqueue("app.worker.process_job", name, payload, retry=3)
            return {"queued": True, "queue": name, "job_id": job.id, "backend": "redis"}
        except Exception:
            if cfg.is_production:
                raise
            logger.warning("enqueue_redis_unavailable_falling_back queue=%s", name)
    QUEUES[name].append(payload)
    return {"queued": True, "queue": name, "depth": len(QUEUES[name]), "backend": "memory"}


def choose_agent(db: Session, customer: Customer) -> User | None:
    if customer.assigned_agent_id:
        agent = db.get(User, customer.assigned_agent_id)
        if agent and agent.status == "online" and agent.current_active_chats < agent.max_active_chats:
            return agent
    return db.scalar(
        select(User)
        .where(
            and_(
                User.role == "agent",
                User.status == "online",
                User.current_active_chats < User.max_active_chats,
            )
        )
        .order_by(User.current_active_chats.asc(), User.created_at.asc())
    )


def assign_agent(db: Session, conversation: Conversation, customer: Customer) -> None:
    agent = choose_agent(db, customer)
    if not agent:
        conversation.status = "pending"
        conversation.priority = "high"
        return
    conversation.agent_id = agent.id
    customer.assigned_agent_id = agent.id
    conversation.status = "open"
    agent.current_active_chats = (
        db.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.agent_id == agent.id, Conversation.status == "open"
            )
        )
        or 0
    )


def wants_english(text: str) -> bool:
    lowered = text.lower()
    return (
        "english" in lowered
        or "do not understand bangla" in lowered
        or "don't understand bangla" in lowered
    )


def handover_needed(text: str, confidence: Decimal) -> tuple[bool, str | None]:
    if confidence < Decimal("0.70"):
        return True, "low_ai_confidence"
    lowered = text.lower()
    keywords = [
        "human",
        "agent",
        "complaint",
        "refund",
        "payment",
        "delivery",
        "discount",
        "অভিযোগ",
        "রিফান্ড",
        "ডেলিভারি",
    ]
    for word in keywords:
        if word in lowered:
            return True, f"keyword:{word}"
    return False, None


def ai_reply(
    db: Session, customer: Customer, text: str
) -> tuple[str, Decimal, bool, str | None]:
    """Generate a reply for the customer message.

    Order of attempts:
      1. STOP keyword → unsubscribe (sync, no LLM)
      2. RAG-augmented LLM reply (async, but called via asyncio.run for the
         existing sync caller). Falls through if no KB / no API key.
      3. Legacy keyword-based product-search reply (the original behaviour).
    """
    if text.strip().upper() == "STOP":
        customer.consent_status = "stopped"
        return (
            "You have been unsubscribed from marketing follow-ups.",
            Decimal("0.95"),
            False,
            None,
        )
    customer.preferred_language = (
        "english" if wants_english(text) else customer.preferred_language
    )

    # Attempt RAG-augmented reply first
    rag_text, rag_conf = _try_rag_reply(db, customer, text)
    if rag_text is not None:
        needed, reason = handover_needed(text, rag_conf)
        if needed:
            return (
                HANDOVER_ENGLISH if customer.preferred_language == "english" else HANDOVER_BANGLA,
                rag_conf,
                True,
                reason,
            )
        return (rag_text, rag_conf, False, None)

    # Legacy keyword product-search fallback
    like = f"%{text[:70]}%"
    product = db.scalar(
        select(Product)
        .where(
            Product.is_active.is_(True),
            or_(Product.name.ilike(like), Product.sku.ilike(like)),
        )
        .limit(1)
    )
    confidence = Decimal("0.86") if product else Decimal("0.62")
    needed, reason = handover_needed(text, confidence)
    if needed or product is None:
        return (
            HANDOVER_ENGLISH if customer.preferred_language == "english" else HANDOVER_BANGLA,
            confidence,
            True,
            reason or "no_product_or_kb_match",
        )
    if customer.preferred_language == "english":
        return (
            f"{product.name} is available. Price: {product.price}, stock: {product.stock}. "
            "Please share quantity, full address, and location link.",
            confidence,
            False,
            None,
        )
    return (
        f"{product.name} পাওয়া যাচ্ছে। দাম: {product.price}, স্টক: {product.stock}। "
        "অর্ডারের জন্য পরিমাণ, পূর্ণ ঠিকানা এবং লোকেশন লিংক দিন।",
        confidence,
        False,
        None,
    )


def _try_rag_reply(
    db: Session, customer: Customer, text: str
) -> tuple[str | None, Decimal]:
    """Synchronous wrapper around the async RAG path. Returns (None, 0)
    on any failure or fallback condition so the caller continues to the
    legacy path."""
    import asyncio
    from app.rag.reply import rag_reply

    try:
        rag = asyncio.run(
            rag_reply(db, customer_text=text, customer_language=customer.preferred_language)
        )
    except RuntimeError:
        # asyncio.run inside an existing loop (rare in this code path; the
        # webhook is async but receive_incoming is invoked sync via a
        # threadpool executor). Skip RAG silently.
        return None, Decimal("0")
    except Exception:
        logger.exception("ai_reply_rag_path_failed")
        return None, Decimal("0")
    if rag is None:
        return None, Decimal("0")
    return rag.text, rag.confidence


def is_duplicate_webhook(db: Session, channel: str, channel_message_id: str | None) -> bool:
    """Idempotency guard: returns True if (channel, channel_message_id) already
    seen (and inserts the marker if not). Missing message_id → not deduped."""
    if not channel_message_id:
        return False
    try:
        db.add(WebhookIdempotency(channel=channel, channel_message_id=channel_message_id))
        db.commit()
        return False
    except IntegrityError:
        db.rollback()
        return True


def receive_incoming(
    db: Session,
    msg: IncomingMessage,
    *,
    tenant_id: str | None = None,
) -> Conversation | None:
    """Channel-agnostic ingestion entry point used by webhook router."""
    tenant_id = tenant_id or settings().default_tenant_id or DEFAULT_TENANT_ID

    if is_duplicate_webhook(db, msg.get("channel", "unknown"), msg.get("channel_message_id")):
        WHATSAPP_WEBHOOK.labels(outcome="duplicate").inc()
        return None

    phone = msg.get("from_phone") or ""
    text = msg.get("text") or ""
    if not phone:
        WHATSAPP_WEBHOOK.labels(outcome="no_phone").inc()
        return None

    # Voice note / audio incoming: don't run AI reply inline. Hand off to the
    # voice-stt-queue worker which downloads audio, transcribes, then re-enters
    # the pipeline via receive_whatsapp_text() with the transcribed text.
    if msg.get("message_type") == "voice" and msg.get("media_id"):
        cfg = settings()
        if not cfg.voice_note_enabled:
            WHATSAPP_WEBHOOK.labels(outcome="voice_disabled").inc()
            return None
        enqueue(
            "voice-stt-queue",
            {
                "tenant_id": tenant_id,
                "channel": msg.get("channel", "whatsapp"),
                "channel_message_id": msg.get("channel_message_id"),
                "from_phone": phone,
                "from_name": msg.get("from_name"),
                "media_id": msg.get("media_id"),
                "media_mime": msg.get("media_mime") or "audio/ogg",
                "is_voice_note": bool(msg.get("is_voice_note", True)),
            },
        )
        WHATSAPP_WEBHOOK.labels(outcome="voice_queued").inc()
        return None

    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if not customer:
        customer = Customer(
            tenant_id=tenant_id,
            phone=phone,
            name=msg.get("from_name") or None,
            preferred_language="bangla",
        )
        db.add(customer)
        db.flush()

    conversation = db.scalar(
        select(Conversation).where(
            Conversation.customer_id == customer.id, Conversation.status != "resolved"
        )
    )
    new_conversation = False
    if not conversation:
        conversation = Conversation(
            tenant_id=tenant_id,
            customer_id=customer.id,
            channel=msg.get("channel", "whatsapp"),
            source=msg.get("channel", "whatsapp"),
        )
        db.add(conversation)
        db.flush()
        assign_agent(db, conversation, customer)
        apply_sla_to_conversation(db, conversation)
        new_conversation = True

    db.add(
        Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            sender_type="customer",
            channel=msg.get("channel", "whatsapp"),
            message_body=text,
            whatsapp_message_id=msg.get("channel_message_id"),
        )
    )
    reply, confidence, handover, reason = ai_reply(db, customer, text)
    conversation.last_message = text
    conversation.last_message_at = datetime.utcnow()
    if handover:
        conversation.handover_required = True
        conversation.handover_reason = reason
        enqueue("human-handover-queue", {"conversation_id": conversation.id, "reason": reason})
    db.add(
        Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            sender_type="ai",
            channel=msg.get("channel", "whatsapp"),
            message_body=reply,
            ai_confidence=confidence,
        )
    )
    if conversation.first_response_at is None:
        conversation.first_response_at = datetime.utcnow()
    enqueue("ai-reply-queue", {"conversation_id": conversation.id})
    enqueue("whatsapp-send-queue", {"to": phone, "text": reply})
    db.commit()

    publish_inbox_event(
        conversation.agent_id,
        {
            "type": "conversation.new" if new_conversation else "message.received",
            "conversation_id": conversation.id,
            "customer_phone": phone,
            "preview": (text or "")[:120],
        },
    )
    WHATSAPP_WEBHOOK.labels(outcome="ok").inc()
    return conversation


# Backwards-compatible wrapper for existing callers / tests.
def receive_whatsapp_text(
    db: Session, phone: str, text: str, message_id: str | None = None
) -> Conversation | None:
    return receive_incoming(
        db,
        IncomingMessage(
            channel="whatsapp",
            channel_message_id=message_id,
            from_phone=phone,
            from_name=None,
            text=text,
        ),
    )
