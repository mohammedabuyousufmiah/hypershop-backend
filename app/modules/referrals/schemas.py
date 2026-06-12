"""Pydantic v2 wire schemas for the referrals module."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReferralMineOut(BaseModel):
    """Shape returned by GET /referrals/mine — matches the original
    ``ReferralMineWire`` shape declared in @ecom/types so we don't have
    to update the FE wire contract."""

    code: str | None
    active_referral_id: str | None
    total_referrals: int
    rewarded_referrals: int
    pending_referrals: int


class ReferralAttributeIn(BaseModel):
    """Body of POST /referrals/attribute — fired by the FE during the
    first auto-login after signup if the ``?ref=`` cookie is set."""

    code: str = Field(..., min_length=4, max_length=16)


class ReferralAttributeAck(BaseModel):
    accepted: bool
    referrer_user_id: UUID | None = None
    reason: str | None = None


class ReferralAttributionOut(BaseModel):
    """Admin-visible row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    referrer_user_id: UUID
    referee_user_id: UUID
    status: str
    rewarded_at: datetime | None
    rewarded_by_order_id: UUID | None
    created_at: datetime
