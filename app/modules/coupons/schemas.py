"""Coupon wire schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CouponOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    description: str | None
    discount_type: str
    discount_value_minor: int
    min_subtotal_minor: int
    max_discount_minor: int | None
    max_total_uses: int | None
    max_uses_per_customer: int
    total_uses: int
    valid_from: datetime | None
    valid_until: datetime | None
    is_active: bool
    created_at: datetime


class CouponListOut(BaseModel):
    items: list[CouponOut]
    total: int


class CouponCreateIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=32, pattern=r"^[A-Z0-9._-]+$")
    description: str | None = Field(default=None, max_length=255)
    discount_type: Literal["PERCENT", "FIXED"]
    discount_value_minor: int = Field(..., gt=0)
    min_subtotal_minor: int = Field(default=0, ge=0)
    max_discount_minor: int | None = Field(default=None, gt=0)
    max_total_uses: int | None = Field(default=None, gt=0)
    max_uses_per_customer: int = Field(default=1, gt=0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_active: bool = True


class CouponPreviewIn(BaseModel):
    """Customer-facing: apply a code against a known subtotal for the
    storefront to preview the discount before checkout-confirm fires."""

    code: str = Field(..., min_length=2, max_length=32)
    subtotal_minor: int = Field(..., ge=0)


class CouponPreviewOut(BaseModel):
    code: str
    valid: bool
    reason: str | None
    discount_minor: int
