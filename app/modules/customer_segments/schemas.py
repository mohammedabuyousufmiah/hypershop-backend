"""Pydantic v2 schemas for customer_segments API surface."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RfmScoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_user_id: UUID
    recency_days: int
    frequency_count: int
    monetary_minor: int
    recency_score: int
    frequency_score: int
    monetary_score: int
    rfm_total: int
    segment_code: str
    last_order_at: datetime | None
    computed_at: datetime


class SegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name_en: str
    name_bn: str
    description: str | None
    rule: dict[str, Any]
    estimated_size: int | None
    is_active: bool
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class SegmentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name_en: str = Field(min_length=1, max_length=120)
    name_bn: str = Field(min_length=1, max_length=120)
    description: str | None = None
    rule: dict[str, Any]


class SegmentUpdate(BaseModel):
    name_en: str | None = Field(default=None, max_length=120)
    name_bn: str | None = Field(default=None, max_length=120)
    description: str | None = None
    rule: dict[str, Any] | None = None
    is_active: bool | None = None


class SegmentMembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    segment_id: UUID
    customer_user_id: UUID
    added_at: datetime


class AudienceExportRead(BaseModel):
    segment_id: UUID
    segment_code: str
    row_count: int
    format: str


__all__ = [
    "RfmScoreRead",
    "SegmentRead",
    "SegmentCreate",
    "SegmentUpdate",
    "SegmentMembershipRead",
    "AudienceExportRead",
]
