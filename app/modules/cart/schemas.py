"""Pydantic wire shapes for cart endpoints.

These intentionally MATCH the storefront's expected wire types
(`CartWire`, `CartItemWire`, `CartQuoteWire`, etc. in
``packages/types/src/index.ts``) field-for-field so the frontend
normaliser (``packages/api-client/src/normalise.ts``) doesn't need
any new tolerance code.

Notable contract decisions:
  * Frontend calls it ``offer_id``; we have variants only, so
    ``offer_id == variant_id`` on the wire.
  * Decimals are emitted as strings (``"1999.50"``) to avoid float
    coercion in JS; the frontend's ``decimalStringToMinor`` parses
    them.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------- inbound ----------


class CartCreateIn(BaseModel):
    """Body for POST /cart and POST /cart/guest (bootstrap)."""
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    model_config = ConfigDict(extra="forbid")


class CartItemAddIn(BaseModel):
    """Body for POST /cart/items and POST /cart/guest/items."""
    offer_id: UUID
    quantity: int = Field(default=1, ge=1, le=99)
    model_config = ConfigDict(extra="forbid")


class CartItemUpdateIn(BaseModel):
    """Body for PATCH /cart/items/{id}. quantity=0 is rejected — caller
    must use DELETE to remove a line."""
    quantity: int = Field(ge=1, le=99)
    model_config = ConfigDict(extra="forbid")


class CartMergeIn(BaseModel):
    """Body for POST /cart/merge — promote a guest cart into the logged-in
    user's cart. Items are upserted by variant_id; quantities sum,
    clamped to ``max_qty_per_line``."""
    session_token: str = Field(min_length=8, max_length=64)
    model_config = ConfigDict(extra="forbid")


# ---------- outbound ----------


class CartItemOut(BaseModel):
    id: UUID
    offer_id: UUID  # = variant_id on the wire
    product_id: UUID
    seller_id: UUID | None
    quantity: int
    price_snapshot: str  # decimal-as-string
    currency: str
    added_at: datetime


class CartOut(BaseModel):
    id: UUID
    user_id: UUID | None
    session_token: str | None
    currency: str
    country_code: str | None
    is_active: bool
    items: list[CartItemOut]


class CartLineQuoteOut(BaseModel):
    item_id: UUID
    offer_id: UUID
    quantity: int
    price_snapshot: str
    unit_price_current: str
    line_total_current: str
    currency: str
    price_changed: bool
    out_of_stock: bool
    offer_inactive: bool


class CartQuoteOut(BaseModel):
    cart_id: UUID
    currency: str
    lines: list[CartLineQuoteOut]
    subtotal: str
    has_price_changes: bool
    has_stock_issues: bool
    line_count: int
    item_count: int


class CartLimitsOut(BaseModel):
    max_qty_per_line: int
    max_lines_per_cart: int
