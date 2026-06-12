"""Customer-care ORM models, integrated into Hypershop.

DESIGN NOTES (2026-05-13 integration)
-------------------------------------
Original CC app shipped with its own ``users`` / ``customers`` /
``products`` / ``orders`` tables. In Hypershop those entities already
exist in their respective modules — duplicating them would split the
source of truth. So the integration:

* DROPS CC's ``User`` model entirely. Agents are Hypershop ``users``
  with the new ``customercare`` role. Per-agent customer-care state
  (status, capacity, language) lives on ``cc_agent_profile`` keyed
  by ``user_id``.

* DROPS CC's ``Customer`` model. Conversations link directly to
  Hypershop ``customers.id``. CC-specific fields (preferred_language,
  consent_status, assigned_agent_id, last_interest) live on
  ``cc_customer_profile`` keyed by ``customer_id``.

* DROPS CC's ``Product``. CC's product-lookup paths go to Hypershop's
  catalog ``products`` table.

* DROPS CC's ``Order``. CC's draft/confirm flow now calls Hypershop's
  orders module. Conversation row keeps only an ``order_id`` FK to
  the canonical orders table.

* All remaining CC tables are prefixed ``cc_`` to avoid collision.

* Multi-tenant ``tenant_id`` columns dropped — Hypershop is
  single-tenant.

* Primary keys switched to PostgreSQL UUID (matches the rest of
  Hypershop's modules).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


# ---------------------------------------------------------------- agent_profile
class CCAgentProfile(Base):
    """CC-specific per-user state for users acting as customer-care agents.

    PK = user_id (1:1 with Hypershop ``users``). A user becomes an agent
    when they're granted the ``customercare`` role AND a row exists here.
    """

    __tablename__ = "cc_agent_profile"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="offline")
    max_active_chats: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    current_active_chats: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language_skill: Mapped[str] = mapped_column(String(40), nullable=False, default="bangla")
    # Per-agent SIP softphone credentials (added 2026-05-16, alembic 0062).
    # Plaintext for now under the rationale that SBC extension passwords
    # are short-lived + not reused as Hypershop user credentials. Column
    # named ``_enc`` to reserve the at-rest-encryption upgrade path.
    sip_extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sip_password_enc: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


# -------------------------------------------------------------- customer_profile
class CCCustomerProfile(Base):
    """CC-specific per-customer state, sitting alongside Hypershop ``customers``.

    PK = customer_id (1:1). Stores fields that don't belong in the
    core customer record: preferred language, consent state, CC
    agent assignment, last-interest tag.
    """

    __tablename__ = "cc_customer_profile"

    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    preferred_language: Mapped[str] = mapped_column(String(40), nullable=False, default="bangla")
    consent_status: Mapped[str] = mapped_column(String(40), nullable=False, default="allowed")
    assigned_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    last_interest: Mapped[str | None] = mapped_column(String(255))
    full_address: Mapped[str | None] = mapped_column(Text)
    location_link: Mapped[str | None] = mapped_column(Text)
    cc_status: Mapped[str] = mapped_column(String(40), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


# -------------------------------------------------------------- conversation
class CCConversation(Base):
    __tablename__ = "cc_conversations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True, nullable=False,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="whatsapp")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="whatsapp")
    last_message: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    handover_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    handover_reason: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(40), nullable=False, default="normal")
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_first_response_breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sla_resolution_breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # FK to Hypershop's orders table — set after a conversation produces an order
    order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


# -------------------------------------------------------------- message
class CCMessage(Base):
    __tablename__ = "cc_messages"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cc_conversations.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    sender_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message_type: Mapped[str] = mapped_column(String(40), nullable=False, default="text")
    message_body: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="whatsapp")
    whatsapp_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    ai_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


# -------------------------------------------------------------- followup
class CCFollowup(Base):
    """Per-customer follow-up campaign tracker (drip messages, retargeting)."""

    __tablename__ = "cc_followups"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
    )
    campaign_name: Mapped[str] = mapped_column(String(160), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


# -------------------------------------------------------------- dead_letter
class CCDeadLetter(Base):
    __tablename__ = "cc_dead_letters"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    source: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


# -------------------------------------------------------------- webhook_idem
class CCWebhookIdempotency(Base):
    __tablename__ = "cc_webhook_idempotency"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    channel: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    channel_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    __table_args__ = (
        UniqueConstraint(
            "channel", "channel_message_id",
            name="uq_cc_webhook_idem_channel_msgid",
        ),
    )


# -------------------------------------------------------------- csat
class CCCsatSurvey(Base):
    __tablename__ = "cc_csat_surveys"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cc_conversations.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    survey_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    score: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


# -------------------------------------------------------------- sla
class CCSlaPolicy(Base):
    __tablename__ = "cc_sla_policies"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[str] = mapped_column(String(40), nullable=False, default="normal")
    first_response_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    resolution_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=240)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


# -------------------------------------------------------------- gdpr
class CCGdprDeletion(Base):
    __tablename__ = "cc_gdpr_deletion_requests"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    customer_phone: Mapped[str | None] = mapped_column(String(40), index=True)
    requested_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


# -------------------------------------------------------------- knowledge (RAG)
class CCKnowledgeDocument(Base):
    __tablename__ = "cc_knowledge_documents"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="text")
    source_url: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    body_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    language: Mapped[str | None] = mapped_column(String(8))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


class CCKnowledgeChunk(Base):
    __tablename__ = "cc_knowledge_chunks"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cc_knowledge_documents.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_body: Mapped[str] = mapped_column("text", Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


# -------------------------------------------------------------- external events
class CCCheckoutEvent(Base):
    """Inbound checkout webhook (storefront / partner integrations).

    Kept for backwards-compat with external callers that POST here.
    Internal Hypershop checkout / order completion flows use the
    in-process outbox handler instead — see ``customer_care/handlers.py``.
    """

    __tablename__ = "cc_checkout_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="storefront", index=True)
    event_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str | None] = mapped_column(String(40))
    customer_phone: Mapped[str | None] = mapped_column(String(40), index=True)
    # Plain string here — the external event_id format isn't a UUID and
    # we don't need referential integrity to Hypershop orders.
    order_external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    raw_payload: Mapped[str | None] = mapped_column(Text)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_cc_checkout_event_provider_eventid"),
    )


class CCPaymentEvent(Base):
    __tablename__ = "cc_payment_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="generic", index=True)
    event_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str | None] = mapped_column(String(40))
    order_external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    customer_phone: Mapped[str | None] = mapped_column(String(40), index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    raw_payload: Mapped[str | None] = mapped_column(Text)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_cc_payment_event_provider_eventid"),
    )


# -------------------------------------------------------------- voice_call
class CCVoiceCall(Base):
    """Inbound voice-call record.

    Provider-neutral: the (provider, provider_call_id) pair carries the
    external reference (Twilio CallSid, Exotel call_sid, etc.); the
    Hypershop UUID ``id`` is the internal handle used everywhere else.

    Status transitions: ringing → assigned → in_call → ended, with
    ``missed`` as an alternative terminal (timed out before assignment).
    """
    __tablename__ = "cc_voice_calls"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    from_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    to_number: Mapped[str | None] = mapped_column(String(32))
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ringing")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    recording_url: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_call_id",
            name="uq_cc_voice_calls_provider_external_id",
        ),
    )


class CCVoiceCallEvent(Base):
    """Append-only state-transition + audit ledger for voice calls."""
    __tablename__ = "cc_voice_call_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    voice_call_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cc_voice_calls.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


Index(
    "ix_cc_dead_letters_status_created",
    CCDeadLetter.status, CCDeadLetter.created_at,
)
Index(
    "ix_cc_knowledge_chunks_doc",
    CCKnowledgeChunk.document_id,
)
