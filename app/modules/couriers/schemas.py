"""Pydantic v2 schemas for the couriers module.

Money fields are integer minor units (paisa). Credentials are masked
on read — only last 4 chars of api_key / api_secret are returned.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CourierCode = Literal["pathao", "redx", "sundarban", "steadfast"]
Environment = Literal["sandbox", "production"]
ShipmentStatus = Literal[
    "created", "pickup_pending", "in_transit", "out_for_delivery",
    "delivered", "returned", "cancelled", "failed", "exception",
]
ServiceType = Literal["regular", "express", "same_day", "next_day"]
RemittanceStatus = Literal["pending", "settled", "disputed", "reconciled"]


# ─── Provider ───────────────────────────────────────────────────────
class CourierProviderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    display_name: str
    country: str
    is_enabled: bool
    supports_cod: bool
    supports_pickup: bool
    supports_return: bool
    coverage_zones: list
    created_at: datetime
    updated_at: datetime


class CourierProviderUpdate(BaseModel):
    is_enabled: bool


# ─── Credentials ────────────────────────────────────────────────────
class CourierCredentialCreate(BaseModel):
    environment: Environment = "sandbox"
    base_url: str = Field(min_length=1, max_length=256)
    api_key: str | None = None
    api_secret: str | None = None
    client_id: str | None = Field(default=None, max_length=128)
    merchant_id: str | None = Field(default=None, max_length=128)
    extra_config: dict | None = None
    is_active: bool = True


class CourierCredentialUpdate(BaseModel):
    environment: Environment | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=256)
    api_key: str | None = None
    api_secret: str | None = None
    client_id: str | None = Field(default=None, max_length=128)
    merchant_id: str | None = Field(default=None, max_length=128)
    extra_config: dict | None = None
    is_active: bool | None = None


def _mask(value: str | None) -> str | None:
    """Return last 4 chars masked with leading ``*``s, or None."""
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return ("*" * (len(value) - 4)) + value[-4:]


class CourierCredentialRead(BaseModel):
    """Masked read view — never returns the full api_key / api_secret."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider_code: str
    environment: str
    base_url: str
    api_key_masked: str | None = None
    api_secret_masked: str | None = None
    client_id: str | None = None
    merchant_id: str | None = None
    extra_config: dict | None = None
    is_active: bool
    last_validated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_masked(cls, row) -> "CourierCredentialRead":
        return cls(
            id=row.id,
            provider_code=row.provider_code,
            environment=row.environment,
            base_url=row.base_url,
            api_key_masked=_mask(row.api_key),
            api_secret_masked=_mask(row.api_secret),
            client_id=row.client_id,
            merchant_id=row.merchant_id,
            extra_config=row.extra_config,
            is_active=row.is_active,
            last_validated_at=row.last_validated_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ─── Shipment ───────────────────────────────────────────────────────
class CourierShipmentCreate(BaseModel):
    order_id: UUID
    provider_code: CourierCode
    service_type: ServiceType = "regular"
    is_cod: bool = False
    cod_amount_minor: int = Field(default=0, ge=0)
    pickup_address: dict
    delivery_address: dict
    items: list[dict] = Field(default_factory=list)


class CourierShipmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_id: UUID
    provider_code: str
    provider_shipment_id: str | None
    tracking_number: str | None
    status: str
    service_type: str
    is_cod: bool
    cod_amount_minor: int
    shipping_charge_minor: int
    label_url: str | None
    pickup_address: dict | None
    delivery_address: dict | None
    provider_response: dict | None
    created_at: datetime
    updated_at: datetime
    dispatched_at: datetime | None
    delivered_at: datetime | None


class CourierShipmentUpdate(BaseModel):
    status: ShipmentStatus | None = None
    tracking_number: str | None = None
    label_url: str | None = None


class ShipmentCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


# ─── Status event ───────────────────────────────────────────────────
class CourierStatusEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shipment_id: UUID | None
    provider_code: str
    provider_shipment_id: str | None
    event_type: str
    mapped_status: str | None
    raw_payload: dict | None
    received_at: datetime


# ─── COD remittance ─────────────────────────────────────────────────
class CourierCodRemittanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider_code: str
    shipment_id: UUID
    cod_amount_minor: int
    courier_fee_minor: int
    settled_amount_minor: int
    settlement_reference: str | None
    status: str
    settled_at: datetime | None
    created_at: datetime


class CourierCodRemittanceCreate(BaseModel):
    provider_code: CourierCode
    shipment_id: UUID
    cod_amount_minor: int = Field(ge=0)
    courier_fee_minor: int = Field(default=0, ge=0)
    settled_amount_minor: int = Field(ge=0)
    settlement_reference: str | None = Field(default=None, max_length=128)
    status: RemittanceStatus = "pending"
