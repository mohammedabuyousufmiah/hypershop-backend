"""Checkout session ORM models.

A checkout session is a short-lived row that:

1. is born DRAFT from a cart snapshot,
2. accepts edits (address, shipping option, optional loyalty redemption),
3. confirms into a real ``orders`` row.

We snapshot the line items + totals into ``snapshot_json`` so that
mutations on the underlying cart between preview and confirm don't
silently change what the customer is buying. ``status`` is the state
machine: DRAFT -> CONFIRMED | CANCELLED | EXPIRED.

We do NOT persist line items as separate rows here — those become
``order_lines`` on confirm via the orders module.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class CheckoutStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class CheckoutSession(Base, TimestampMixin):
    __tablename__ = "checkout_sessions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # nullable for guest checkouts (session_token-only flow)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    cart_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("carts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=CheckoutStatus.DRAFT.value,
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="BDT"
    )
    # totals breakdown (always re-computed at preview time)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    shipping_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default="0"
    )
    tax_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default="0"
    )
    discount_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default="0"
    )
    loyalty_redeemed: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default="0"
    )
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # address + shipping option are stored as JSON so we don't need to
    # tightly couple this module to the deliveries module's address
    # schema. ``preview`` validates the shape.
    shipping_address_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    billing_address_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    payment_method: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="cod"
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # frozen line snapshot — list of {variant_id, product_id, quantity,
    # unit_price, currency, name} dicts
    snapshot_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # populated on CONFIRMED
    order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','confirmed','cancelled','expired')",
            name="status_enum",
        ),
        Index("ix_checkout_sessions_user", "user_id"),
        Index("ix_checkout_sessions_cart", "cart_id"),
        Index("ix_checkout_sessions_status", "status"),
    )
