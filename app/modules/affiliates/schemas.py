"""Pydantic v2 wire schemas for affiliates."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AffiliateRecordOut(BaseModel):
    """``GET /affiliates/me``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    commission_rate: Decimal
    enabled: bool
    total_conversions: int
    total_commission_minor: int
    created_at: datetime


class AffiliateLinkOut(BaseModel):
    code: str
    url: str
    commission_rate: Decimal
    product_slug: str


class AffiliateStatsOut(BaseModel):
    code: str
    enabled: bool
    commission_rate: Decimal
    total_conversions: int
    total_commission_minor: int
    currency: str = "BDT"


class AffiliateCreditIn(BaseModel):
    """Internal — fired by the checkout-confirm hook when the caller's
    own ``?aff=CODE`` cookie matches an affiliate record (typically the
    caller's own, since the same customer rarely buys via their own
    link — but the endpoint is permissive)."""

    code: str = Field(..., min_length=4, max_length=16)
    subtotal_minor: int = Field(..., ge=0)
    order_id: UUID
