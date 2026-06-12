"""Pydantic schemas for cart_recovery API surface."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChannelLiteral = Literal["whatsapp", "email", "push", "sms", "all"]
ReasonLiteral = Literal[
    "opted_out", "bounced", "complained", "frequency_cap",
    "recent_purchase", "manual",
]


def _mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return value
    name, _, domain = value.partition("@")
    head = name[:1] if name else ""
    return f"{head}***@{domain}"


def _mask_phone(value: str | None) -> str | None:
    if not value:
        return value
    s = value.strip()
    if len(s) <= 6:
        return s[:2] + "***"
    return s[:4] + "***" + s[-3:]


class SuppressionCreate(BaseModel):
    customer_user_id: UUID | None = None
    key_email: str | None = Field(default=None, max_length=256)
    key_phone: str | None = Field(default=None, max_length=32)
    channel: ChannelLiteral = "all"
    reason: ReasonLiteral = "manual"
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _need_one_key(self) -> SuppressionCreate:
        if not (self.customer_user_id or self.key_email or self.key_phone):
            raise ValueError(
                "At least one of customer_user_id, key_email, key_phone required.",
            )
        return self


class SuppressionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_user_id: UUID | None
    channel: str
    reason: str
    key_email: str | None
    key_phone: str | None
    expires_at: datetime | None
    created_at: datetime

    @classmethod
    def from_orm_masked(cls, row) -> SuppressionRead:
        return cls(
            id=row.id,
            customer_user_id=row.customer_user_id,
            channel=row.channel,
            reason=row.reason,
            key_email=_mask_email(row.key_email),
            key_phone=_mask_phone(row.key_phone),
            expires_at=row.expires_at,
            created_at=row.created_at,
        )


class DispatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cart_id: UUID | None
    customer_user_id: UUID | None
    milestone: str
    channel: str
    template_code: str
    status: str
    suppression_reason: str | None
    failure_reason: str | None
    sent_at: datetime | None
    recovered_at: datetime | None
    recovered_order_id: UUID | None
    created_at: datetime


class OptOutRequest(BaseModel):
    key_email: str | None = Field(default=None, max_length=256)
    key_phone: str | None = Field(default=None, max_length=32)
    channel: ChannelLiteral = "all"

    @model_validator(mode="after")
    def _need_one_key(self) -> OptOutRequest:
        if not (self.key_email or self.key_phone):
            raise ValueError("At least one of key_email or key_phone required.")
        return self


class OptOutResponse(BaseModel):
    ok: bool
    already_suppressed: bool


__all__ = [
    "SuppressionCreate",
    "SuppressionRead",
    "DispatchRead",
    "OptOutRequest",
    "OptOutResponse",
]
