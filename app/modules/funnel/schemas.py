"""Pydantic v2 schemas — verbatim from the source zip."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SourceType = Literal["social", "website", "app", "whatsapp", "email", "ads"]
ConsentSource = Literal[
    "checkout", "signup", "profile", "whatsapp", "manual_admin", "unknown",
]


class TrackEventRequest(BaseModel):
    external_customer_id: str = Field(..., min_length=3, max_length=128)
    idempotency_key: str = Field(..., min_length=8, max_length=160)

    hypershop_customer_id: Optional[int] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    source: SourceType
    event_name: str = Field(..., min_length=2, max_length=64)

    product_id: Optional[str] = None
    category_id: Optional[str] = None
    campaign_id: Optional[str] = None
    session_id: Optional[str] = None

    value: float = 0
    metadata: dict[str, Any] = {}

    marketing_consent: Optional[bool] = None
    whatsapp_consent: Optional[bool] = None
    sms_consent: Optional[bool] = None
    ad_retargeting_consent: Optional[bool] = None
    consent_source: ConsentSource = "unknown"


class TrackEventResponse(BaseModel):
    accepted: bool
    duplicate: bool = False
    customer_id: int
    external_customer_id: str
    event_name: str
    added_score: int
    total_score: int
    segment: str
    recommended_action: str
    privacy_notice: str
