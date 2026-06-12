"""Tax rule wire schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TaxRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    country: str
    category_slug: str | None
    rate_bps: int
    is_active: bool
    created_at: datetime


class TaxRuleListOut(BaseModel):
    items: list[TaxRuleOut]
    total: int


class TaxRuleCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    country: str = Field(default="BD", min_length=2, max_length=2)
    category_slug: str | None = Field(default=None, max_length=120)
    rate_bps: int = Field(..., ge=0, le=10_000)
    is_active: bool = True


class TaxPreviewIn(BaseModel):
    subtotal_minor: int = Field(..., ge=0)
    country: str = Field(default="BD", min_length=2, max_length=2)
    category_slug: str | None = Field(default=None, max_length=120)


class TaxPreviewOut(BaseModel):
    subtotal_minor: int
    rate_bps: int
    tax_minor: int
    total_minor: int
    matched_rule: UUID | None
