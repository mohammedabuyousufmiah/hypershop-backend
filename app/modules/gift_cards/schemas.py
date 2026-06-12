"""Pydantic schemas for gift cards."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def _normalise_code(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


class GiftCardCheckIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class GiftCardCheckOut(BaseModel):
    """Public preview — face value + status, no PII."""

    valid: bool
    face_value_minor: int | None
    currency: str | None
    status: str | None
    expires_at: datetime | None


class GiftCardRedeemIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class GiftCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    face_value_minor: int
    currency: str
    status: str
    redeemed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class GiftCardListOut(BaseModel):
    items: list[GiftCardOut]
    total: int
