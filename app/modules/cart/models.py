"""Cart + cart_items ORM models.

Two parallel surfaces share the same tables:

* **Authenticated cart** — keyed on ``user_id``. One open cart per user
  is enforced by a partial unique index.
* **Guest cart** — keyed on a server-minted ``session_token``. The token
  travels in the ``X-Cart-Session`` HTTP header and on every guest body.

A cart row carries the currency + country code only; line totals are
derived (NOT stored on the cart header) so the cart price stays in
sync with the latest variant pricing every time the storefront calls
``/cart/quote``. The ``price_snapshot`` column on ``cart_items`` is the
price-at-add-time used to detect drift between add-to-cart and
checkout — the quote endpoint diffs against the current variant price
and surfaces a ``price_changed`` flag per line.

Status lifecycle:
    open     → live cart, customer can mutate
    merged   → guest cart that got rolled into the user's open cart
    abandoned→ explicit DELETE /cart, OR auto-set if untouched > TTL
    converted→ cart became an Order via /checkout/{id}/confirm
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
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class CartStatus(StrEnum):
    OPEN = "open"
    MERGED = "merged"
    ABANDONED = "abandoned"
    CONVERTED = "converted"


class Cart(Base, TimestampMixin):
    __tablename__ = "carts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Exactly one of ``user_id`` / ``session_token`` is set on any open
    # cart. user_id-bound carts survive logout; session_token carts are
    # ephemeral and merge into a user-bound cart at login.
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    session_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=CartStatus.OPEN.value,
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="BDT"
    )
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    items: Mapped[list["CartItem"]] = relationship(
        "CartItem",
        back_populates="cart",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CartItem.added_at",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','merged','abandoned','converted')",
            name="status_enum",
        ),
        CheckConstraint(
            "(user_id IS NOT NULL) OR (session_token IS NOT NULL)",
            name="owner_present",
        ),
        # One open cart per identity. Partial unique indexes so closed
        # carts (history) don't block creating a new open one.
        Index(
            "ux_carts_open_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'open' AND user_id IS NOT NULL"),
        ),
        Index(
            "ux_carts_open_session",
            "session_token",
            unique=True,
            postgresql_where=text("status = 'open' AND session_token IS NOT NULL"),
        ),
        Index("ix_carts_status", "status"),
    )


class CartItem(Base):
    """One line per (cart, variant). Repeated add-to-cart of the same
    variant increments ``quantity`` rather than inserting a new row.

    ``price_snapshot`` captures the unit price at the moment the item
    was added so the storefront can surface "price changed" UX on the
    quote endpoint. ``currency`` is denormalised to make line totals
    self-describing even when joining historical data.
    """

    __tablename__ = "cart_items"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    cart_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("carts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # variant_id is the canonical key — the frontend wire calls it
    # `offer_id` because the catalog ships seller offers per variant.
    # We don't have an offers table; one variant == one offer in this
    # build.
    variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seller_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="SET NULL"),
        nullable=True,
    )
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    price_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="BDT"
    )
    added_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
        onupdate=text("(now() AT TIME ZONE 'UTC')"),
    )

    cart: Mapped[Cart] = relationship("Cart", back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        UniqueConstraint("cart_id", "variant_id", name="cart_variant"),
        Index("ix_cart_items_cart_id", "cart_id"),
    )
