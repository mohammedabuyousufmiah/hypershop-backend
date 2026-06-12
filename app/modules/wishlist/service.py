"""Wishlist service — add / remove / list / contains.

Schema is intentionally tiny (one table, composite-unique). All
business rules live here:

  * ``add`` is idempotent — re-adding an existing (user, product) pair
    silently no-ops instead of raising. Frontends can fire the heart
    button without an "already in wishlist?" check.
  * ``remove`` is forgiving — non-existent pair returns False, not a
    404. Same reason.
  * ``list_for_user`` joins ``products`` + first ``product_media`` +
    cheapest active ``product_variants`` so the customer-web wishlist
    page renders cards on a single round-trip.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Product, ProductMedia, ProductVariant
from app.modules.wishlist.models import WishlistItem


class WishlistService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, *, user_id: UUID, product_id: UUID) -> WishlistItem:
        """Add a product to the user's wishlist. Idempotent on (user, product)."""
        # Validate that the product exists + is active. Block stale or
        # archived product_ids from sneaking into the wishlist via API.
        product = (
            await self.session.execute(
                select(Product).where(Product.id == product_id),
            )
        ).scalar_one_or_none()
        if product is None or product.status != "active":
            from app.core.errors import NotFoundError
            raise NotFoundError("Product not found or not available.")

        # Idempotent insert — return the existing row if (user, product)
        # already in the table.
        existing = (
            await self.session.execute(
                select(WishlistItem).where(
                    WishlistItem.user_id == user_id,
                    WishlistItem.product_id == product_id,
                ),
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        row = WishlistItem(user_id=user_id, product_id=product_id)
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError:
            # Concurrent insert from another tab / device. Refetch.
            await self.session.rollback()
            row = (
                await self.session.execute(
                    select(WishlistItem).where(
                        WishlistItem.user_id == user_id,
                        WishlistItem.product_id == product_id,
                    ),
                )
            ).scalar_one()
        return row

    async def remove(self, *, user_id: UUID, product_id: UUID) -> bool:
        """Remove a (user, product) pair. Returns True if a row was removed."""
        row = (
            await self.session.execute(
                select(WishlistItem).where(
                    WishlistItem.user_id == user_id,
                    WishlistItem.product_id == product_id,
                ),
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def contains(self, *, user_id: UUID, product_id: UUID) -> bool:
        row = (
            await self.session.execute(
                select(WishlistItem.id).where(
                    WishlistItem.user_id == user_id,
                    WishlistItem.product_id == product_id,
                ),
            )
        ).scalar_one_or_none()
        return row is not None

    async def list_for_user(
        self, *, user_id: UUID, offset: int = 0, limit: int = 50,
    ) -> tuple[list[dict], int]:
        """Return wishlist rows with denormalised product fields.

        The output of each row is a plain dict (NOT a Pydantic model)
        so the router can hand it directly to ``WishlistItemOut`` via
        ``model_validate``. Keeping it as a dict avoids a tight coupling
        between the service and the schema layer.
        """
        total = int(
            (
                await self.session.execute(
                    select(func.count(WishlistItem.id)).where(
                        WishlistItem.user_id == user_id,
                    ),
                )
            ).scalar_one()
            or 0,
        )
        if total == 0:
            return [], 0

        # Page rows.
        rows = (
            (
                await self.session.execute(
                    select(WishlistItem, Product)
                    .join(Product, Product.id == WishlistItem.product_id)
                    .where(WishlistItem.user_id == user_id)
                    .order_by(WishlistItem.created_at.desc())
                    .offset(offset)
                    .limit(limit),
                )
            )
            .all()
        )

        product_ids = [p.id for _, p in rows]
        # First image per product — position ASC.
        media_map: dict[UUID, str] = {}
        if product_ids:
            media_rows = (
                await self.session.execute(
                    # ProductMedia uses ``url`` (see catalog/models.py
                    # line 258). ``media_url`` was the old column name
                    # — renamed in a catalog refactor but this service
                    # was not updated, so POST /wishlist + GET /wishlist
                    # always 500'd with AttributeError.
                    select(ProductMedia.product_id, ProductMedia.url)
                    .where(ProductMedia.product_id.in_(product_ids))
                    .order_by(ProductMedia.product_id, ProductMedia.position),
                )
            ).all()
            for pid, url in media_rows:
                if pid not in media_map and url:
                    media_map[pid] = url

        # Cheapest active variant per product (price + stock indicator).
        price_map: dict[UUID, tuple[int, str, bool]] = {}
        if product_ids:
            var_rows = (
                await self.session.execute(
                    select(
                        ProductVariant.product_id,
                        ProductVariant.price,
                        ProductVariant.is_active,
                    )
                    .where(ProductVariant.product_id.in_(product_ids))
                    .where(ProductVariant.is_active.is_(True))
                    .order_by(ProductVariant.product_id, ProductVariant.price),
                )
            ).all()
            for pid, price, _is_active in var_rows:
                if pid not in price_map:
                    price_map[pid] = (
                        int(price * 100),  # Decimal major → minor units
                        "BDT",
                        True,
                    )

        out: list[dict] = []
        for wi, p in rows:
            price_tup = price_map.get(p.id)
            out.append(
                {
                    "id": wi.id,
                    "product_id": p.id,
                    "product_slug": p.slug,
                    "product_title": p.name,
                    "product_image_url": media_map.get(p.id),
                    "price_minor": price_tup[0] if price_tup else None,
                    "currency": price_tup[1] if price_tup else p.base_currency,
                    "in_stock": price_tup[2] if price_tup else False,
                    "created_at": wi.created_at,
                },
            )
        return out, total
