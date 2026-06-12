"""Feature flag wire shapes."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FeatureFlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    description: str | None
    is_enabled: bool
    rollout_percent: int
    created_at: datetime
    updated_at: datetime


class FeatureFlagListOut(BaseModel):
    items: list[FeatureFlagOut]
    total: int


class FeatureFlagCreateIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9._-]+$")
    description: str | None = Field(default=None, max_length=500)
    is_enabled: bool = False
    rollout_percent: int = Field(default=100, ge=0, le=100)


class FeatureFlagUpdateIn(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    is_enabled: bool | None = None
    rollout_percent: int | None = Field(default=None, ge=0, le=100)


class FeatureFlagEvaluateIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    subject_id: str | None = Field(default=None, max_length=64)


class FeatureFlagEvaluateOut(BaseModel):
    key: str
    enabled: bool
    rollout_percent: int
