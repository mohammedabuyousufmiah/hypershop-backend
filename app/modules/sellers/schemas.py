"""Pydantic schemas for the sellers module — phase 1 (admin only)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.sellers.codes import (
    ALL_PAYOUT_CADENCES,
    ALL_PAYOUT_METHODS,
    ALL_SELLER_ROLES,
)


class SellerCreateIn(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=200)
    slug: str = Field(..., min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=32)


class SellerKycSubmitIn(BaseModel):
    """Phase-1 minimum KYC = TIN + NID + bank details.

    Trade license is optional at this layer — admin can demand it
    in the rejection reason when a seller's GMV is large enough to
    cross the VAT threshold.
    """

    tin: str = Field(..., min_length=4, max_length=32)
    nid: str = Field(..., min_length=4, max_length=32)
    bank_account_name: str = Field(..., min_length=2, max_length=200)
    bank_account_number: str = Field(..., min_length=4, max_length=32)
    bank_name: str = Field(..., min_length=2, max_length=120)
    bank_branch: str | None = Field(None, max_length=120)
    trade_license_no: str | None = Field(None, max_length=64)


class SellerRejectIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class SellerSuspendIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class SellerCommissionUpdateIn(BaseModel):
    commission_percent: Decimal = Field(..., ge=0, le=100, max_digits=5, decimal_places=2)


class SellerPayoutConfigIn(BaseModel):
    payout_cadence: str | None = None
    payout_method: str | None = None
    payout_account_id: str | None = Field(None, max_length=64)


class SellerUserLinkIn(BaseModel):
    user_id: UUID
    role: str = Field(..., max_length=16)


class SellerOut(BaseModel):
    """Shape returned to admin endpoints — full row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_name: str
    slug: str
    contact_email: str | None
    contact_phone: str | None
    status: str
    tin: str | None
    nid: str | None
    trade_license_no: str | None
    bank_account_name: str | None
    bank_account_number: str | None
    bank_name: str | None
    bank_branch: str | None
    commission_percent: Decimal
    payout_cadence: str
    payout_method: str
    payout_account_id: str | None
    rejection_reason: str | None
    moderated_by: UUID | None
    moderated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SellerListOut(BaseModel):
    items: list[SellerOut]
    total: int


class SellerUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seller_id: UUID
    user_id: UUID
    role: str
    created_at: datetime
