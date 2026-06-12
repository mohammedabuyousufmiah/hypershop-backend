"""CartService — all cart mutating + read logic for the customer-facing
storefront. Two parallel surfaces share this class:

* Authenticated calls reach it with ``user_id`` set.
* Guest calls reach it with ``session_token`` set.

Either path resolves to the same ``Cart`` row underneath; the rest of
the API is identical. ``merge_guest_into_user`` is the only method that
spans both: at login the storefront calls it to fold guest items into
the freshly-authenticated user's cart.
"""
from __future__ import annotations

import secrets
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.errors import NotFoundError, ValidationError
from app.modules.cart.models import Cart, CartItem, CartStatus
from app.modules.catalog.models import Product, ProductVariant


# Storefront UI sets these expectations; mirrored on the frontend
# ``CartLimitsWire`` shape so a single source of truth.
MAX_QTY_PER_LINE = 99
MAX_LINES_PER_CART = 50


def _new_session_token() -> str:
    """URL-safe random token used as the guest cart's identity."""
    return secrets.token_urlsafe(32)


class CartService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- internal lookups ------------------------------------------------

    async def _open_cart_for_user(self, user_id: UUID) -> Cart | None:
        stmt = select(Cart).where(
            Cart.user_id == user_id, Cart.status == CartStatus.OPEN.value
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _open_cart_for_session(self, session_token: str) -> Cart | None:
        stmt = select(Cart).where(
            Cart.session_token == session_token,
            Cart.status == CartStatus.OPEN.value,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _variant_for_offer(self, offer_id: UUID) -> ProductVariant:
        stmt = select(ProductVariant).where(ProductVariant.id == offer_id)
        v = (await self.session.execute(stmt)).scalar_one_or_none()
        if v is None:
            raise NotFoundError("Offer not found.")
        if not v.is_active:
            raise ValidationError("Offer is no longer available.")
        return v

    # ---- bootstrap -------------------------------------------------------

    async def get_or_create_for_user(
        self, user_id: UUID, *, currency: str, country_code: str | None
    ) -> Cart:
        cart = await self._open_cart_for_user(user_id)
        if cart is not None:
            return cart
        cart = Cart(
            user_id=user_id,
            session_token=None,
            currency=currency,
            country_code=country_code,
            status=CartStatus.OPEN.value,
        )
        self.session.add(cart)
        await self.session.flush()
        # Mark the items relationship as loaded-and-empty WITHOUT
        # firing the lazy loader. Using `cart.items = []` triggers a
        # SELECT for the old value first, which crashes once the
        # async session context has unwound (MissingGreenlet).
        set_committed_value(cart, "items", [])
        return cart

    async def create_guest(
        self, *, currency: str, country_code: str | None
    ) -> Cart:
        cart = Cart(
            user_id=None,
            session_token=_new_session_token(),
            currency=currency,
            country_code=country_code,
            status=CartStatus.OPEN.value,
        )
        self.session.add(cart)
        await self.session.flush()
        # Explicitly initialise the relationship so the serialiser
        # doesn't lazy-load after the async session closes.
        # Mark the items relationship as loaded-and-empty WITHOUT
        # firing the lazy loader. Using `cart.items = []` triggers a
        # SELECT for the old value first, which crashes once the
        # async session context has unwound (MissingGreenlet).
        set_committed_value(cart, "items", [])
        return cart

    async def get_for_user(self, user_id: UUID) -> Cart | None:
        return await self._open_cart_for_user(user_id)

    async def get_for_session(self, session_token: str) -> Cart:
        cart = await self._open_cart_for_session(session_token)
        if cart is None:
            raise NotFoundError("Cart not found.")
        return cart

    # ---- item mutations --------------------------------------------------

    async def add_item(
        self,
        cart: Cart,
        *,
        offer_id: UUID,
        quantity: int,
    ) -> CartItem:
        if quantity < 1 or quantity > MAX_QTY_PER_LINE:
            raise ValidationError(
                f"Quantity must be 1..{MAX_QTY_PER_LINE}."
            )
        variant = await self._variant_for_offer(offer_id)

        # Upsert: if the line exists, add to it (clamped); else insert.
        existing: CartItem | None = next(
            (i for i in cart.items if i.variant_id == offer_id), None
        )
        if existing is not None:
            new_qty = min(MAX_QTY_PER_LINE, existing.quantity + quantity)
            existing.quantity = new_qty
            await self.session.flush()
            return existing

        if len(cart.items) >= MAX_LINES_PER_CART:
            raise ValidationError(
                f"Cart has reached the {MAX_LINES_PER_CART}-line limit. "
                "Remove an item to add a new one.",
            )

        item = CartItem(
            cart_id=cart.id,
            variant_id=variant.id,
            product_id=variant.product_id,
            seller_id=None,  # populated post-fetch with product.seller_id
            quantity=quantity,
            price_snapshot=variant.price,
            currency=cart.currency,
        )
        # Resolve seller via product
        prod = await self.session.get(Product, variant.product_id)
        if prod is not None:
            item.seller_id = prod.seller_id
        self.session.add(item)
        await self.session.flush()
        # Re-attach via relationship for caller convenience
        cart.items.append(item)
        return item

    async def update_item(
        self, cart: Cart, *, item_id: UUID, quantity: int
    ) -> CartItem:
        if quantity < 1 or quantity > MAX_QTY_PER_LINE:
            raise ValidationError(
                f"Quantity must be 1..{MAX_QTY_PER_LINE}."
            )
        item = next((i for i in cart.items if i.id == item_id), None)
        if item is None:
            raise NotFoundError("Cart item not found.")
        item.quantity = quantity
        await self.session.flush()
        return item

    async def remove_item(self, cart: Cart, *, item_id: UUID) -> None:
        item = next((i for i in cart.items if i.id == item_id), None)
        if item is None:
            raise NotFoundError("Cart item not found.")
        await self.session.delete(item)
        await self.session.flush()
        cart.items[:] = [i for i in cart.items if i.id != item_id]

    async def clear(self, cart: Cart) -> None:
        """Mark the cart abandoned. We do NOT delete the row so order
        history + analytics keep the audit trail."""
        cart.status = CartStatus.ABANDONED.value
        for it in list(cart.items):
            await self.session.delete(it)
        cart.items.clear()
        await self.session.flush()

    # ---- quote -----------------------------------------------------------

    async def quote(self, cart: Cart) -> dict:
        """Re-fetch current variant prices and emit a per-line diff."""
        if not cart.items:
            return {
                "cart_id": cart.id,
                "currency": cart.currency,
                "lines": [],
                "subtotal": "0.00",
                "has_price_changes": False,
                "has_stock_issues": False,
                "line_count": 0,
                "item_count": 0,
            }

        variant_ids = {i.variant_id for i in cart.items}
        stmt = select(ProductVariant).where(ProductVariant.id.in_(variant_ids))
        variants_by_id = {
            v.id: v for v in (await self.session.execute(stmt)).scalars().all()
        }

        lines = []
        subtotal = Decimal("0.00")
        has_changes = False
        has_stock = False
        item_count = 0
        for it in cart.items:
            v = variants_by_id.get(it.variant_id)
            current_price = v.price if v is not None else it.price_snapshot
            offer_inactive = v is None or not v.is_active
            line_total = current_price * it.quantity
            subtotal += line_total
            price_changed = current_price != it.price_snapshot
            if price_changed:
                has_changes = True
            if offer_inactive:
                has_stock = True
            item_count += it.quantity
            lines.append(
                {
                    "item_id": it.id,
                    "offer_id": it.variant_id,
                    "quantity": it.quantity,
                    "price_snapshot": f"{it.price_snapshot:.2f}",
                    "unit_price_current": f"{current_price:.2f}",
                    "line_total_current": f"{line_total:.2f}",
                    "currency": it.currency,
                    "price_changed": price_changed,
                    "out_of_stock": False,  # stock check deferred to Phase B-2
                    "offer_inactive": offer_inactive,
                }
            )
        return {
            "cart_id": cart.id,
            "currency": cart.currency,
            "lines": lines,
            "subtotal": f"{subtotal:.2f}",
            "has_price_changes": has_changes,
            "has_stock_issues": has_stock,
            "line_count": len(lines),
            "item_count": item_count,
        }

    # ---- merge -----------------------------------------------------------

    async def merge_guest_into_user(
        self, *, user_id: UUID, session_token: str
    ) -> Cart:
        """Combine a guest cart's items into the user's open cart.

        Strategy:
          1. Load the guest cart (must exist + be open + session-token-only).
          2. Get-or-create the user's open cart (carries the user's currency).
          3. For each guest line, upsert into the user cart by variant.
          4. Mark the guest cart as ``merged`` so the same token can't
             be replayed.
        """
        guest = await self._open_cart_for_session(session_token)
        if guest is None:
            raise NotFoundError("Guest cart not found or already merged.")
        if guest.user_id is not None:
            raise ValidationError("Cart already belongs to a user.")

        user_cart = await self.get_or_create_for_user(
            user_id, currency=guest.currency, country_code=guest.country_code
        )

        for line in list(guest.items):
            existing = next(
                (i for i in user_cart.items if i.variant_id == line.variant_id),
                None,
            )
            if existing is not None:
                existing.quantity = min(
                    MAX_QTY_PER_LINE, existing.quantity + line.quantity
                )
            else:
                if len(user_cart.items) >= MAX_LINES_PER_CART:
                    break
                # detach from guest, re-attach to user cart
                line.cart_id = user_cart.id
                user_cart.items.append(line)
        guest.items.clear()
        guest.status = CartStatus.MERGED.value
        await self.session.flush()
        return user_cart


def cart_to_out(cart: Cart) -> dict:
    """Wire serialisation matching ``packages/types/src/index.ts:CartWire``."""
    return {
        "id": cart.id,
        "user_id": cart.user_id,
        "session_token": cart.session_token,
        "currency": cart.currency,
        "country_code": cart.country_code,
        "is_active": cart.status == CartStatus.OPEN.value,
        "items": [item_to_out(i) for i in sorted(cart.items, key=lambda x: x.added_at)],
    }


def item_to_out(it: CartItem) -> dict:
    return {
        "id": it.id,
        "offer_id": it.variant_id,
        "product_id": it.product_id,
        "seller_id": it.seller_id,
        "quantity": it.quantity,
        "price_snapshot": f"{it.price_snapshot:.2f}",
        "currency": it.currency,
        "added_at": it.added_at,
    }
