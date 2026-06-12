"""Pydantic schemas for the wishlist module."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WishlistAddIn(BaseModel):
    product_id: UUID


class WishlistItemOut(BaseModel):
    """Wishlist row as the customer-web renders it.

    The product fields are denormalised from the catalogue at read time
    so the list-page query is one round-trip. They are NOT stored on
    ``wishlist_items``; they're joined from ``products`` on each fetch.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    product_slug: str
    product_title: str
    product_image_url: str | None
    price_minor: int | None
    currency: str | None
    in_stock: bool
    created_at: datetime


class WishlistOut(BaseModel):
    items: list[WishlistItemOut]
    total: int = Field(ge=0)


class WishlistContainsOut(BaseModel):
    """Heart-icon state read on the PDP — single boolean, no list cost."""

    in_wishlist: bool
