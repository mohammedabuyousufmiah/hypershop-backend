from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field, HttpUrl, field_validator, model_validator

from app.core.validation import StrictModel
from app.modules.catalog.sku import is_valid_barcode

# ---------------- Brand ----------------


class BrandCreate(StrictModel):
    name: str = Field(..., min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1024)
    logo_url: HttpUrl | None = None
    is_active: bool = True


class BrandUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1024)
    logo_url: HttpUrl | None = None
    is_active: bool | None = None


class BrandResponse(StrictModel):
    id: UUID
    name: str
    slug: str
    description: str | None = None
    logo_url: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------- Category ----------------


class CategoryCreate(StrictModel):
    name: str = Field(..., min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: UUID | None = None
    description: str | None = Field(default=None, max_length=2048)
    sort_order: int = Field(default=0, ge=0, le=100_000)
    is_active: bool = True


class CategoryUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: UUID | None = None
    description: str | None = Field(default=None, max_length=2048)
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None


class CategoryResponse(StrictModel):
    id: UUID
    parent_id: UUID | None
    name: str
    slug: str
    description: str | None = None
    sort_order: int
    is_active: bool
    created_at: datetime


class CategoryNode(StrictModel):
    id: UUID
    parent_id: UUID | None
    name: str
    slug: str
    sort_order: int
    is_active: bool
    children: list[CategoryNode] = Field(default_factory=list)


# ---------------- Variant ----------------


class VariantCreate(StrictModel):
    # SKU is optional on input — service layer auto-generates as
    # ``{mother_sku}-V{idx:03d}`` when omitted.
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    barcode: str | None = Field(default=None, max_length=64)
    options: dict[str, str] = Field(default_factory=dict)
    price: Decimal = Field(..., max_digits=14, decimal_places=2, ge=0)
    compare_at_price: Decimal | None = Field(default=None, max_digits=14, decimal_places=2, ge=0)
    cost: Decimal | None = Field(default=None, max_digits=14, decimal_places=2, ge=0)
    weight_grams: int | None = Field(default=None, ge=0, le=10_000_000)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True

    @field_validator("compare_at_price")
    @classmethod
    def _gte_price(cls, v: Decimal | None, info: Any) -> Decimal | None:
        if v is None:
            return v
        price = info.data.get("price")
        if price is not None and v < price:
            raise ValueError("compare_at_price must be >= price")
        return v

    @field_validator("barcode")
    @classmethod
    def _check_barcode(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not is_valid_barcode(v):
            raise ValueError(
                "barcode must be 8–64 alphanumeric chars (EAN-8/13, UPC, GTIN, Code128)",
            )
        return v


class VariantUpdate(StrictModel):
    sku: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    barcode: str | None = Field(default=None, max_length=64)
    options: dict[str, str] | None = None
    price: Decimal | None = Field(default=None, max_digits=14, decimal_places=2, ge=0)
    compare_at_price: Decimal | None = Field(default=None, max_digits=14, decimal_places=2, ge=0)
    cost: Decimal | None = Field(default=None, max_digits=14, decimal_places=2, ge=0)
    weight_grams: int | None = Field(default=None, ge=0, le=10_000_000)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None

    @field_validator("barcode")
    @classmethod
    def _check_barcode(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not is_valid_barcode(v):
            raise ValueError(
                "barcode must be 8–64 alphanumeric chars (EAN-8/13, UPC, GTIN, Code128)",
            )
        return v


class VariantResponse(StrictModel):
    id: UUID
    sku: str
    name: str | None = None
    barcode: str | None = None
    options: dict[str, str]
    price: Decimal
    compare_at_price: Decimal | None = None
    weight_grams: int | None = None
    sort_order: int
    is_active: bool


# ---------------- Media ----------------


class MediaCreate(StrictModel):
    url: HttpUrl
    alt: str | None = Field(default=None, max_length=255)
    kind: str = Field(default="image", pattern=r"^(image|video)$")
    position: int = Field(default=0, ge=0, le=1000)
    variant_id: UUID | None = None


class MediaResponse(StrictModel):
    id: UUID
    url: str
    alt: str | None = None
    kind: str
    position: int
    variant_id: UUID | None = None


# ---------------- Product ----------------


class ProductCreate(StrictModel):
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    name: str = Field(..., min_length=1, max_length=200)
    short_description: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=8192)
    brand_id: UUID | None = None
    category_id: UUID | None = None
    base_currency: str = Field(..., min_length=3, max_length=3)
    tax_class: str = Field(default="standard", min_length=1, max_length=32)
    attributes: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="draft", pattern=r"^(draft|active|archived)$")
    variants: list[VariantCreate] = Field(..., min_length=1, max_length=200)
    media: list[MediaCreate] = Field(default_factory=list, max_length=64)

    # Pharma fields. requires_prescription is mandatory whenever is_medicine=true
    # (the model validator below enforces that). For non-medicine products it
    # defaults to False but may be passed explicitly.
    is_medicine: bool = False
    requires_prescription: bool | None = None
    generic_name: str | None = Field(default=None, max_length=200)
    strength: str | None = Field(default=None, max_length=64)
    dosage_form: str | None = Field(default=None, max_length=64)
    expires_at: datetime | None = None

    @field_validator("base_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _check_medicine_rules(self) -> ProductCreate:
        if self.is_medicine:
            missing: list[str] = []
            if not self.generic_name or not self.generic_name.strip():
                missing.append("generic_name")
            if not self.strength or not self.strength.strip():
                missing.append("strength")
            if self.brand_id is None:
                missing.append("brand_id")
            if self.requires_prescription is None:
                missing.append("requires_prescription")
            if missing:
                raise ValueError(
                    "medicine products require: " + ", ".join(missing),
                )
        return self


class ProductUpdate(StrictModel):
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    short_description: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=8192)
    brand_id: UUID | None = None
    category_id: UUID | None = None
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_class: str | None = Field(default=None, min_length=1, max_length=32)
    attributes: dict[str, Any] | None = None
    status: str | None = Field(default=None, pattern=r"^(draft|active|archived)$")
    requires_prescription: bool | None = None
    generic_name: str | None = Field(default=None, max_length=200)
    strength: str | None = Field(default=None, max_length=64)
    dosage_form: str | None = Field(default=None, max_length=64)
    expires_at: datetime | None = None


class ProductBlockRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=255)


class ProductExpiryRequest(StrictModel):
    expires_at: datetime | None = None


class ProductResponse(StrictModel):
    id: UUID
    slug: str
    name: str
    mother_sku: str
    short_description: str | None = None
    description: str | None = None
    status: str
    base_currency: str
    tax_class: str
    attributes: dict[str, Any]
    brand: BrandResponse | None = None
    category: CategoryResponse | None = None
    variants: list[VariantResponse]
    media: list[MediaResponse]
    is_medicine: bool
    requires_prescription: bool
    generic_name: str | None = None
    strength: str | None = None
    dosage_form: str | None = None
    expires_at: datetime | None = None
    blocked_at: datetime | None = None
    blocked_reason: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProductSummary(StrictModel):
    id: UUID
    slug: str
    name: str
    mother_sku: str
    status: str
    base_currency: str
    brand_name: str | None = None
    category_name: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    primary_image_url: str | None = None
    is_medicine: bool = False
    requires_prescription: bool = False


# Pydantic v2 forward-ref resolution for the recursive node.
CategoryNode.model_rebuild()
