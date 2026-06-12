"""Pydantic wire schemas for analytics."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalyticsTrackIn(BaseModel):
    """Anonymous-capable: the browser sends an ``anonymous_id`` always,
    and ``customer_user_id`` is patched server-side from the bearer
    token when one is present."""

    anonymous_id: str = Field(..., min_length=1, max_length=64)
    event_type: str = Field(..., min_length=1, max_length=64)
    properties: dict = Field(default_factory=dict)
    session_id: str | None = Field(default=None, max_length=64)


class AnalyticsTrackAck(BaseModel):
    id: UUID
    accepted_at: datetime


class AnalyticsEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    anonymous_id: str
    customer_user_id: UUID | None
    event_type: str
    properties: dict
    session_id: str | None
    created_at: datetime


class AnalyticsEventListOut(BaseModel):
    items: list[AnalyticsEventOut]
    total: int
