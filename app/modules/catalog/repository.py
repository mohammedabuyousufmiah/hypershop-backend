from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from decimal import Decimal

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.core.time import utc_now
from app.modules.catalog.models import (
    Brand,
    Category,
    Product,
    ProductMedia,
    ProductStatus,
    ProductVariant,
)


def _public_visibility_filter() -> tuple:
    """SQL conditions a product must satisfy to be visible to the public.

    Status-active is checked separately by callers because the admin search
    sometimes wants drafts. Block + expiry, however, are universally hidden
    from the customer-facing API.
    """
    now = utc_now()
    return (
        Product.blocked_at.is_(None),
        or_(Product.expires_at.is_(None), Product.expires_at > now),
    )


class BrandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, brand_id: UUID) -> Brand | None:
        return await self.session.get(Brand, brand_id)

    async def get_by_slug(self, slug: str) -> Brand | None:
        return (
            await self.session.execute(select(Brand).where(Brand.slug == slug))
        ).scalar_one_or_none()

    async def list_active(self) -> Sequence[Brand]:
        stmt = select(Brand).where(Brand.is_active.is_(True)).order_by(Brand.name)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_all(self) -> Sequence[Brand]:
        return (await self.session.execute(select(Brand).order_by(Brand.name))).scalars().all()

    async def create(self, **fields: object) -> Brand:
        b = Brand(**fields)
        self.session.add(b)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Brand name or slug already exists.") from e
        return b

    async def update(self, brand_id: UUID, **fields: object) -> Brand:
        b = await self.session.get(Brand, brand_id)
        if b is None:
            raise NotFoundError("Brand not found.")
        for k, v in fields.items():
            if v is not None:
                setattr(b, k, v)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Brand name or slug already exists.") from e
        return b

    async def delete(self, brand_id: UUID) -> None:
        b = await self.session.get(Brand, brand_id)
        if b is None:
            raise NotFoundError("Brand not found.")
        await self.session.delete(b)
        await self.session.flush()


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, category_id: UUID) -> Category | None:
        return await self.session.get(Category, category_id)

    async def get_by_slug(
        self,
        slug: str,
        parent_id: UUID | None = None,
    ) -> Category | None:
        stmt = select(Category).where(
            and_(Category.slug == slug, Category.parent_id == parent_id),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> Sequence[Category]:
        return (
            (
                await self.session.execute(
                    select(Category).order_by(
                        Category.parent_id.is_(None).desc(),
                        Category.sort_order,
                        Category.name,
                    ),
                )
            )
            .scalars()
            .all()
        )

    async def has_children(self, category_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(Category)
            .where(
                Category.parent_id == category_id,
            )
        )
        return ((await self.session.execute(stmt)).scalar_one() or 0) > 0

    async def has_products(self, category_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(Product)
            .where(
                Product.category_id == category_id,
            )
        )
        return ((await self.session.execute(stmt)).scalar_one() or 0) > 0

    async def create(self, **fields: object) -> Category:
        c = Category(**fields)
        self.session.add(c)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError(
                "Category slug already exists under this parent.",
            ) from e
        return c

    async def update(self, category_id: UUID, **fields: object) -> Category:
        c = await self.session.get(Category, category_id)
        if c is None:
            raise NotFoundError("Category not found.")
        for k, v in fields.items():
            if v is not None:
                setattr(c, k, v)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError(
                "Category slug already exists under this parent.",
            ) from e
        return c

    async def delete(self, category_id: UUID) -> None:
        c = await self.session.get(Category, category_id)
        if c is None:
            raise NotFoundError("Category not found.")
        await self.session.delete(c)
        await self.session.flush()


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, product_id: UUID) -> Product | None:
        stmt = (
            select(Product)
            .where(Product.id == product_id)
            .options(
                selectinload(Product.variants),
                selectinload(Product.media),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Product | None:
        stmt = (
            select(Product)
            .where(Product.slug == slug)
            .options(
                selectinload(Product.variants),
                selectinload(Product.media),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search(
        self,
        *,
        offset: int,
        limit: int,
        q: str | None,
        category_slug: str | None,
        brand_slug: str | None,
        status_filter: ProductStatus | None,
        public_only: bool = False,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        has_discount: bool = False,
    ) -> tuple[Sequence[Product], int]:
        base = select(Product)
        count_base = select(func.count()).select_from(Product)

        conds = []
        if status_filter is not None:
            conds.append(Product.status == status_filter)
        if public_only:
            conds.extend(_public_visibility_filter())
        if q:
            like = f"%{q.lower()}%"
            conds.append(
                or_(
                    func.lower(Product.name).like(like),
                    func.lower(Product.search_text).like(like),
                ),
            )
        if category_slug:
            base = base.join(Category, Product.category_id == Category.id)
            count_base = count_base.join(Category, Product.category_id == Category.id)
            conds.append(Category.slug == category_slug)
        if brand_slug:
            base = base.join(Brand, Product.brand_id == Brand.id)
            count_base = count_base.join(Brand, Product.brand_id == Brand.id)
            conds.append(Brand.slug == brand_slug)
        if min_price is not None or max_price is not None:
            # Match if ANY active variant's price falls in the requested band.
            variant_conds = [
                ProductVariant.product_id == Product.id,
                ProductVariant.is_active.is_(True),
            ]
            if min_price is not None:
                variant_conds.append(ProductVariant.price >= min_price)
            if max_price is not None:
                variant_conds.append(ProductVariant.price <= max_price)
            conds.append(
                exists(select(ProductVariant.id).where(and_(*variant_conds))),
            )
        if has_discount:
            # Match if ANY active variant has compare_at_price > price.
            # Powers "Mega Deal" + "Trending Now" deal rows.
            conds.append(
                exists(
                    select(ProductVariant.id).where(
                        and_(
                            ProductVariant.product_id == Product.id,
                            ProductVariant.is_active.is_(True),
                            ProductVariant.compare_at_price.is_not(None),
                            ProductVariant.compare_at_price > ProductVariant.price,
                        ),
                    ),
                ),
            )
        if conds:
            base = base.where(*conds)
            count_base = count_base.where(*conds)

        total = (await self.session.execute(count_base)).scalar_one() or 0
        stmt = (
            base.options(selectinload(Product.variants), selectinload(Product.media))
            .order_by(Product.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return rows, int(total)

    async def slug_exists(self, slug: str) -> bool:
        stmt = select(Product.id).where(Product.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def mother_sku_exists(self, mother_sku: str) -> bool:
        stmt = select(Product.id).where(Product.mother_sku == mother_sku)
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def get_publicly_visible_by_slug(self, slug: str) -> Product | None:
        stmt = (
            select(Product)
            .where(
                Product.slug == slug,
                Product.status == ProductStatus.ACTIVE,
                *_public_visibility_filter(),
            )
            .options(selectinload(Product.variants), selectinload(Product.media))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def clear_block(self, product_id: UUID) -> None:
        p = await self.session.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        p.blocked_at = None
        p.blocked_reason = None
        await self.session.flush()

    async def set_expiry(self, product_id: UUID, expires_at: datetime | None) -> None:
        p = await self.session.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        p.expires_at = expires_at
        await self.session.flush()

    async def create(self, **fields: object) -> Product:
        p = Product(**fields)
        self.session.add(p)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Product slug already exists.") from e
        return p

    async def update(self, product_id: UUID, **fields: object) -> Product:
        p = await self.session.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        for k, v in fields.items():
            if v is not None:
                setattr(p, k, v)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Product slug already exists.") from e
        return p

    async def archive(self, product_id: UUID) -> None:
        p = await self.session.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        p.status = ProductStatus.ARCHIVED
        await self.session.flush()


class VariantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, variant_id: UUID) -> ProductVariant | None:
        return await self.session.get(ProductVariant, variant_id)

    async def get_by_sku(self, sku: str) -> ProductVariant | None:
        return (
            await self.session.execute(
                select(ProductVariant).where(ProductVariant.sku == sku),
            )
        ).scalar_one_or_none()

    async def create(self, **fields: object) -> ProductVariant:
        v = ProductVariant(**fields)
        self.session.add(v)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Variant SKU or barcode already exists.") from e
        return v

    async def update(self, variant_id: UUID, **fields: object) -> ProductVariant:
        v = await self.session.get(ProductVariant, variant_id)
        if v is None:
            raise NotFoundError("Variant not found.")
        for k, val in fields.items():
            if val is not None:
                setattr(v, k, val)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Variant SKU or barcode already exists.") from e
        return v

    async def delete(self, variant_id: UUID) -> None:
        v = await self.session.get(ProductVariant, variant_id)
        if v is None:
            raise NotFoundError("Variant not found.")
        await self.session.delete(v)
        await self.session.flush()


class MediaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **fields: object) -> ProductMedia:
        m = ProductMedia(**fields)
        self.session.add(m)
        await self.session.flush()
        return m

    async def delete(self, media_id: UUID) -> None:
        m = await self.session.get(ProductMedia, media_id)
        if m is None:
            raise NotFoundError("Media not found.")
        await self.session.delete(m)
        await self.session.flush()

    async def delete_all_for_product(self, product_id: UUID) -> None:
        await self.session.execute(
            delete(ProductMedia).where(ProductMedia.product_id == product_id),
        )
