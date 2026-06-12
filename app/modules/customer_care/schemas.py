"""Pydantic schemas for the customer-care module."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ---------------- Agent profile ----------------


class AgentProfileResponse(StrictModel):
    user_id: UUID
    email: str | None
    full_name: str | None
    status: str
    max_active_chats: int
    current_active_chats: int
    language_skill: str


class AgentStatusUpdate(StrictModel):
    status: str = Field(..., pattern=r"^(online|busy|away|offline)$")


# ---------------- Conversation ----------------


class MessageResponse(StrictModel):
    id: UUID
    conversation_id: UUID
    sender_type: str
    message_type: str
    message_body: str | None
    media_url: str | None
    channel: str
    whatsapp_message_id: str | None
    ai_confidence: Decimal | None
    created_at: datetime


class ConversationSummary(StrictModel):
    id: UUID
    customer_id: UUID
    customer_phone: str | None
    agent_id: UUID | None
    channel: str
    status: str
    priority: str
    last_message: str | None
    last_message_at: datetime
    handover_required: bool
    sla_first_response_breached: bool
    sla_resolution_breached: bool
    unread_count: int = 0


class ConversationDetail(ConversationSummary):
    customer_name: str | None
    preferred_language: str | None
    consent_status: str | None
    source: str
    first_response_at: datetime | None
    resolved_at: datetime | None
    handover_reason: str | None
    order_id: UUID | None
    messages: list[MessageResponse]


class SendMessageRequest(StrictModel):
    body: str = Field(..., min_length=1, max_length=4096)
    message_type: str = Field(default="text", pattern=r"^(text|image|template)$")
    media_url: str | None = None


class TransferConversationRequest(StrictModel):
    target_agent_id: UUID
    reason: str | None = Field(default=None, max_length=500)


class ResolveConversationRequest(StrictModel):
    resolution_note: str | None = Field(default=None, max_length=1000)


# ---------------- Customer profile ----------------


class CustomerProfileResponse(StrictModel):
    customer_id: UUID
    full_name: str | None
    phone: str | None
    preferred_language: str
    consent_status: str
    assigned_agent_id: UUID | None
    last_interest: str | None
    cc_status: str


# ---------------- Followup ----------------


class FollowupCreate(StrictModel):
    customer_id: UUID
    product_id: UUID | None = None
    campaign_name: str = Field(..., min_length=1, max_length=160)
    next_followup_at: datetime | None = None


class FollowupResponse(StrictModel):
    id: UUID
    customer_id: UUID
    product_id: UUID | None
    campaign_name: str
    stage: int
    status: str
    last_sent_at: datetime | None
    next_followup_at: datetime | None


# ---------------- Dashboard ----------------


class DashboardSummary(StrictModel):
    open_conversations: int
    unassigned_conversations: int
    handover_required: int
    sla_breached: int
    online_agents: int
    total_agents: int
    csat_avg_last_30d: Decimal | None
    pending_followups: int
