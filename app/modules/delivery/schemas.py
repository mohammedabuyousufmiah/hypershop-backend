from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.validation import StrictModel


SERVICE_AREA_PRICE = Decimal("50.00")
THREEPL_MIN = Decimal("70.00")
THREEPL_MAX = Decimal("150.00")


def _enforce_kind_price(kind: str, price: Decimal) -> None:
    if kind == "service_area" and price != SERVICE_AREA_PRICE:
        raise ValueError(
            f"service_area zones must be priced at {SERVICE_AREA_PRICE} BDT (got {price})",
        )
    if kind == "3pl" and not (THREEPL_MIN <= price <= THREEPL_MAX):
        raise ValueError(
            f"3pl zones must be priced between {THREEPL_MIN}–{THREEPL_MAX} BDT (got {price})",
        )


# ---------------- Admin: zone CRUD ----------------


class DeliveryZoneCreate(StrictModel):
    code: str = Field(..., min_length=1, max_length=48, pattern=r"^[A-Z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=160)
    kind: str = Field(..., pattern=r"^(service_area|3pl)$")
    price: Decimal = Field(..., max_digits=14, decimal_places=2, ge=0)
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    cities: list[str] = Field(default_factory=list, max_length=200)
    postal_codes: list[str] = Field(default_factory=list, max_length=200)
    is_default: bool = False
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10_000)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("cities", "postal_codes")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]

    @model_validator(mode="after")
    def _check_price_band(self) -> DeliveryZoneCreate:
        _enforce_kind_price(self.kind, self.price)
        return self


class DeliveryZoneUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    kind: str | None = Field(default=None, pattern=r"^(service_area|3pl)$")
    price: Decimal | None = Field(default=None, max_digits=14, decimal_places=2, ge=0)
    cities: list[str] | None = Field(default=None, max_length=200)
    postal_codes: list[str] | None = Field(default=None, max_length=200)
    is_default: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("cities", "postal_codes")
    @classmethod
    def _strip(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [s.strip() for s in v if s and s.strip()]


class DeliveryZoneResponse(StrictModel):
    id: UUID
    code: str
    name: str
    kind: str
    price: Decimal
    currency: str
    cities: list[str]
    postal_codes: list[str]
    is_default: bool
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


# ---------------- Quote ----------------


class QuoteAddress(StrictModel):
    city: str = Field(..., min_length=1, max_length=120)
    postal_code: str | None = Field(default=None, max_length=16)
    country: str = Field(default="BD", min_length=2, max_length=3)


class QuoteRequest(StrictModel):
    address: QuoteAddress
    payment_method: str = Field(default="cod", pattern=r"^(cod|online)$")


class QuoteResponse(StrictModel):
    zone_code: str
    zone_name: str
    kind: str
    base_fee: Decimal
    cod_fee: Decimal  # always 0 by current rule, surfaced for transparency
    total: Decimal
    currency: str
