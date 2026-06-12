"""Pydantic schemas for disputes API surface."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DisputeTypeLiteral = Literal[
    "wrong_item", "damaged", "not_received", "quality_issue",
    "fake_item", "billing_error", "other",
]
StatusLiteral = Literal[
    "open", "awaiting_seller", "awaiting_buyer", "under_review",
    "resolved", "closed",
]
ResolutionLiteral = Literal[
    "refund_full", "refund_partial", "replace", "decline", "customer_withdrew",
]
RoleLiteral = Literal["buyer", "seller", "mediator", "system"]
UploaderRoleLiteral = Literal["buyer", "seller", "mediator"]
EscrowStatusLiteral = Literal[
    "active", "released_to_buyer", "released_to_seller", "split", "cancelled",
]


# ─── Dispute lifecycle ─────────────────────────────────────────────


class DisputeOpenRequest(BaseModel):
    order_id: UUID
    seller_id: UUID
    order_item_id: UUID | None = None
    dispute_type: DisputeTypeLiteral
    subject: str = Field(min_length=3, max_length=256)
    description: str | None = Field(default=None, max_length=8000)
    amount_disputed_minor: int = Field(ge=0)


class DisputeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_id: UUID
    order_item_id: UUID | None
    opened_by_user_id: UUID
    seller_id: UUID
    dispute_type: str
    status: str
    resolution: str | None
    amount_disputed_minor: int
    amount_refunded_minor: int
    subject: str
    description: str | None
    mediator_user_id: UUID | None
    decision_notes: str | None
    opened_at: datetime
    last_response_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DisputeListResponse(BaseModel):
    items: list[DisputeRead]
    total: int
    limit: int
    offset: int


# ─── Messages ──────────────────────────────────────────────────────


class DisputeMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class DisputeMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dispute_id: UUID
    author_user_id: UUID | None
    author_role: str
    body: str
    attachments: list[Any]
    is_system: bool
    created_at: datetime


# ─── Evidence ──────────────────────────────────────────────────────


class EvidenceUploadRequest(BaseModel):
    file_url: str = Field(min_length=1, max_length=4096)
    content_type: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=512)


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dispute_id: UUID
    uploaded_by_user_id: UUID
    uploader_role: str
    file_url: str
    content_type: str
    size_bytes: int
    description: str | None
    created_at: datetime


# ─── Seller actions ────────────────────────────────────────────────


class SellerCounterOfferRequest(BaseModel):
    counter_amount_minor: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=4000)


# ─── Mediator actions ──────────────────────────────────────────────


class MediatorAssignRequest(BaseModel):
    mediator_user_id: UUID
    internal_note: str | None = Field(default=None, max_length=4000)


class MediatorDecisionRequest(BaseModel):
    resolution: ResolutionLiteral
    refund_amount_minor: int = Field(ge=0)
    decision_notes: str = Field(min_length=1, max_length=8000)


class BuyerWithdrawRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


# ─── Escrow ────────────────────────────────────────────────────────


class EscrowHoldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dispute_id: UUID
    seller_id: UUID
    order_id: UUID
    held_amount_minor: int
    status: str
    released_to_buyer_minor: int
    released_to_seller_minor: int
    release_note: str | None
    created_at: datetime
    released_at: datetime | None


class EscrowHoldListResponse(BaseModel):
    items: list[EscrowHoldRead]
    total: int
    limit: int
    offset: int


class MessageListResponse(BaseModel):
    items: list[DisputeMessageRead]
    total: int


class EvidenceListResponse(BaseModel):
    items: list[EvidenceRead]
    total: int
