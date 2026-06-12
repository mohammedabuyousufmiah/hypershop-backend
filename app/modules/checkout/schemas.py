"""Pydantic wire shapes for /checkout endpoints — kept in lockstep
with ``packages/types/src/index.ts:CheckoutSessionWire``."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_serializer


class ShippingAddressIn(BaseModel):
    """Subset of DeliveryAddress used to compute shipping cost and
    persist on the session.

    Field-name compatibility: the canonical backend names are
    ``address_line1`` / ``country``. The customer-web stores the
    field as ``line1`` / ``country_code`` (matching the saved-address
    wire). We accept both via ``AliasChoices`` so the storefront
    submits unchanged. ``extra="ignore"`` so unknown fields like
    ``district`` / ``delivery_notes`` don't 422 — they're either
    folded into ``region`` (district) or stored at the parent level
    (notes)."""
    full_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=8, max_length=32)
    address_line1: str = Field(
        min_length=1, max_length=200,
        validation_alias=AliasChoices("address_line1", "line1"),
    )
    address_line2: str | None = Field(
        default=None, max_length=200,
        validation_alias=AliasChoices("address_line2", "line2"),
    )
    city: str = Field(min_length=1, max_length=120)
    region: str | None = Field(
        default=None, max_length=120,
        validation_alias=AliasChoices("region", "state", "district"),
    )
    postal_code: str | None = Field(default=None, max_length=32)
    country: str = Field(
        default="BD", min_length=2, max_length=3,
        validation_alias=AliasChoices("country", "country_code"),
    )
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class CheckoutPreviewIn(BaseModel):
    """Body for POST /checkout/preview."""
    shipping_address: ShippingAddressIn
    payment_method: str = Field(default="cod", pattern=r"^(cod|online)$")
    note: str | None = Field(
        default=None, max_length=500,
        validation_alias=AliasChoices("note", "notes"),
    )
    use_cart_id: UUID | None = Field(
        default=None,
        description=(
            "Optional: explicit cart id. When omitted the caller's "
            "open cart is used (authenticated) or X-Cart-Session "
            "must be set (guest)."
        ),
    )
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class CheckoutCancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=255)
    model_config = ConfigDict(extra="forbid")


class CheckoutConfirmIn(BaseModel):
    """Confirm body — the session already has the address + payment_method.
    The storefront may pass a final note + payment method token (online
    gateways) + affiliate attribution code. `extra="ignore"` so future
    optional fields don't 422 the storefront."""
    note: str | None = Field(
        default=None, max_length=500,
        validation_alias=AliasChoices("note", "notes"),
    )
    payment_method_token: str | None = Field(default=None, max_length=512)
    affiliate_code: str | None = Field(default=None, max_length=64)
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class CheckoutApplyLoyaltyIn(BaseModel):
    points: int = Field(ge=0, le=10_000_000)
    model_config = ConfigDict(extra="forbid")


# ---- outbound ----


class CheckoutLineOut(BaseModel):
    variant_id: UUID
    product_id: UUID
    name: str
    quantity: int
    unit_price: str
    line_total: str
    currency: str

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        d = handler(self)
        # Add customer-web wire aliases that `fromCheckoutLine` reads.
        d["offer_id"]       = d.get("variant_id")
        d["id"]             = d.get("variant_id")
        d["seller_id"]      = d.get("seller_id") or str(d.get("variant_id"))
        d["warehouse_id"]   = d.get("warehouse_id") or str(d.get("variant_id"))
        d["price_snapshot"] = d.get("unit_price")
        d["price_changed"]  = False
        return d


class CheckoutSessionOut(BaseModel):
    id: UUID
    user_id: UUID | None
    cart_id: UUID
    status: str
    currency: str
    subtotal: str
    shipping_total: str
    tax_total: str
    discount_total: str
    loyalty_redeemed: str
    grand_total: str
    payment_method: str
    note: str | None
    shipping_address: dict[str, Any]
    billing_address: dict[str, Any]
    items: list[CheckoutLineOut]
    order_id: UUID | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        """Emit BOTH the canonical backend field names AND the
        customer-web wire-shape aliases the storefront's
        ``fromCheckoutSession`` normaliser expects. Avoids a breaking
        rename — old API consumers keep reading ``subtotal`` /
        ``grand_total`` / ``items`` while the storefront reads
        ``subtotal_amount`` / ``total_amount`` / ``lines``."""
        d = handler(self)
        d["subtotal_amount"]         = d.get("subtotal")
        d["shipping_amount"]         = d.get("shipping_total")
        d["tax_amount"]              = d.get("tax_total")
        d["discount_amount"]         = d.get("discount_total")
        d["total_amount"]            = d.get("grand_total")
        d["loyalty_discount_amount"] = d.get("loyalty_redeemed") or "0"
        d["loyalty_points_applied"]  = 0
        d["loyalty_redemption_id"]   = None
        d["notes"]                   = d.get("note")
        d["lines"]                   = d.get("items") or []
        d["country_code"]            = (d.get("shipping_address") or {}).get("country") or "BD"
        d["confirmed_at"]            = None
        d["cancelled_at"]            = None
        d["cancellation_reason"]     = None
        d["warnings"]                = []
        return d


class CheckoutLimitsOut(BaseModel):
    max_session_age_hours: int
    cod_enabled: bool
    online_enabled: bool
    supported_payment_methods: list[str]
