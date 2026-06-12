"""Pydantic schemas for customer notifications."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CustomerNotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: str
    title: str
    body: str
    action_url: str | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class CustomerNotificationListOut(BaseModel):
    items: list[CustomerNotificationOut]
    total: int
    unread: int


class MarkReadIn(BaseModel):
    ids: list[UUID] = Field(default_factory=list)
    all: bool = False
