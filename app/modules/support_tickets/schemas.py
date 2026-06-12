"""Pydantic schemas for the customer-facing support_tickets endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─── input ──────────────────────────────────────────────────────────────

class TicketCreateIn(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=5000)
    category: Literal[
        "general", "order", "delivery", "payment", "refund", "account", "other",
    ] = "general"
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    order_id: UUID | None = None


class TicketMessageCreateIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


# ─── output ─────────────────────────────────────────────────────────────

class TicketMessageOut(BaseModel):
    id: UUID
    ticket_id: UUID
    author_user_id: UUID
    author_kind: Literal["customer", "agent"]
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketOut(BaseModel):
    id: UUID
    customer_user_id: UUID
    subject: str
    body: str
    category: str
    priority: str
    status: Literal["open", "in_progress", "waiting_customer", "resolved", "closed"]
    assigned_to: UUID | None
    order_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketDetailOut(TicketOut):
    messages: list[TicketMessageOut]


class TicketListOut(BaseModel):
    items: list[TicketOut]
    total: int
