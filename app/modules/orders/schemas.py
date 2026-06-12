from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator

from app.core.validation import StrictModel


# ---------------- Address ----------------


class DeliveryAddress(StrictModel):
    """Order shipping address.

    Field-name compatibility note (2026-05-13): the checkout module
    stores ``delivery_address`` with keys ``address_line1`` /
    ``address_line2`` (mirroring the storefront's input shape), while
    this schema's canonical names are ``line1`` / ``line2``. We accept
    both via ``AliasChoices`` so the orders serializer can read back
    historical rows AND the place_order request stays compatible.
    """
    recipient_name: str = Field(..., min_length=1, max_length=120)
    phone: str = Field(..., min_length=6, max_length=32)
    line1: str = Field(
        ...,
        min_length=1, max_length=255,
        validation_alias=AliasChoices("line1", "address_line1"),
    )
    line2: str | None = Field(
        default=None, max_length=255,
        validation_alias=AliasChoices("line2", "address_line2"),
    )
    city: str = Field(..., min_length=1, max_length=120)
    postal_code: str | None = Field(default=None, max_length=32)
    country: str = Field(default="BD", min_length=2, max_length=3)

    # The default StrictModel config has ``extra="forbid"``. Loosen it
    # for this one model so historical rows containing extra keys
    # (e.g. ``country_code`` from checkout) still deserialise.
    model_config = {"extra": "ignore", "populate_by_name": True}


# ---------------- Place order ----------------


class OrderItemRequest(StrictModel):
    variant_id: UUID
    quantity: int = Field(..., ge=1, le=10_000)


class PlaceOrderRequest(StrictModel):
    items: list[OrderItemRequest] = Field(..., min_length=1, max_length=200)
    payment_method: str = Field(..., pattern=r"^(cod|online)$")
    delivery_address: DeliveryAddress
    notes: str | None = Field(default=None, max_length=2048)
    currency: str = Field(default="BDT", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


# ---------------- Cancel ----------------


class CancelOrderRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=512)


# ---------------- Transition (admin) ----------------


class StatusTransitionRequest(StrictModel):
    """Used for admin endpoints that transition status with an optional reason."""

    reason: str | None = Field(default=None, max_length=512)


# ---------------- Responses ----------------


class OrderLineResponse(StrictModel):
    id: UUID
    variant_id: UUID
    product_name: str
    variant_sku: str
    requires_prescription: bool
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class OrderStatusEntry(StrictModel):
    from_status: str | None
    to_status: str
    transitioned_at: datetime
    transitioned_by: UUID | None
    reason: str | None


class OrderResponse(StrictModel):
    id: UUID
    code: str
    customer_user_id: UUID
    status: str
    payment_method: str
    requires_prescription: bool
    currency: str
    subtotal: Decimal
    tax_total: Decimal
    shipping_total: Decimal
    discount_total: Decimal
    grand_total: Decimal
    delivery_address: DeliveryAddress
    notes: str | None
    placed_at: datetime
    payment_confirmed_at: datetime | None
    approved_at: datetime | None
    dispatched_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    assigned_pharmacist_id: UUID | None
    lines: list[OrderLineResponse]
    history: list[OrderStatusEntry]
    created_at: datetime
    updated_at: datetime


class OrderSummary(StrictModel):
    id: UUID
    code: str
    status: str
    payment_method: str
    requires_prescription: bool
    grand_total: Decimal
    currency: str
    placed_at: datetime
    customer_user_id: UUID
