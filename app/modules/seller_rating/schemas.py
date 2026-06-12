"""Pydantic schemas for seller rating API."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SellerRatingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seller_id: UUID
    overall_score: Decimal
    tier: str
    on_time_shipping_rate: Decimal | None
    return_rate: Decimal | None
    dispute_resolution_rate: Decimal | None
    response_time_hours: Decimal | None
    review_avg: Decimal | None
    review_count: int
    order_count_30d: int
    last_computed_at: datetime
    created_at: datetime
    updated_at: datetime


class SellerRatingPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seller_id: UUID
    overall_score: Decimal
    tier: str
    review_avg: Decimal | None
    review_count: int


class SellerRatingListResponse(BaseModel):
    items: list[SellerRatingRead]
    total: int
    limit: int
    offset: int


class SellerRatingSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seller_id: UUID
    overall_score: Decimal
    tier: str
    metrics: dict[str, Any]
    computed_at: datetime


class SellerRatingSnapshotListResponse(BaseModel):
    items: list[SellerRatingSnapshotRead]
    total: int


class SuspendRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
