from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError
from app.core.pagination import Page, PageParams
from app.modules.catalog.models import Product, ProductStatus
from app.modules.catalog.repository import (
    BrandRepository,
    CategoryRepository,
    ProductRepository,
)
from app.modules.catalog.schemas import (
    BrandResponse,
    CategoryNode,
    CategoryResponse,
    MediaResponse,
    ProductResponse,
    ProductSummary,
    VariantResponse,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _product_to_response(p: Product) -> ProductResponse:
    return ProductResponse(
        id=p.id,
        slug=p.slug,
        name=p.name,
        mother_sku=p.mother_sku,
        short_description=p.short_description,
        description=p.description,
        status=p.status,
        base_currency=p.base_currency,
        tax_class=p.tax_class,
        attributes=p.attributes,
        brand=BrandResponse.model_validate(p.brand) if p.brand else None,
        category=CategoryResponse.model_validate(p.category) if p.category else None,
        variants=[VariantResponse.model_validate(v) for v in p.variants if v.is_active],
        media=[MediaResponse.model_validate(m) for m in p.media],
        is_medicine=p.is_medicine,
        requires_prescription=p.requires_prescription,
        generic_name=p.generic_name,
        strength=p.strength,
        dosage_form=p.dosage_form,
        expires_at=p.expires_at,
        blocked_at=p.blocked_at,
        blocked_reason=p.blocked_reason,
        published_at=p.published_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _product_to_summary(p: Product) -> ProductSummary:
    prices = [v.price for v in p.variants if v.is_active]
    primary_image = next(
        (m.url for m in sorted(p.media, key=lambda x: x.position) if m.kind == "image"),
        None,
    )
    return ProductSummary(
        id=p.id,
        slug=p.slug,
        name=p.name,
        mother_sku=p.mother_sku,
        status=p.status,
        base_currency=p.base_currency,
        brand_name=p.brand.name if p.brand else None,
        category_name=p.category.name if p.category else None,
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        primary_image_url=primary_image,
        is_medicine=p.is_medicine,
        requires_prescription=p.requires_prescription,
    )


@router.get("/brands", response_model=list[BrandResponse])
async def list_brands(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> list[BrandResponse]:
    async with uow.transactional() as session:
        repo = BrandRepository(session)
        rows = await repo.list_active()
        return [BrandResponse.model_validate(b) for b in rows]


@router.get("/categories", response_model=list[CategoryNode])
async def category_tree(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[CategoryNode]:
    async with uow.transactional() as session:
        repo = CategoryRepository(session)
        rows = list(await repo.list_all())

    by_id: dict = {
        c.id: CategoryNode(
            id=c.id,
            parent_id=c.parent_id,
            name=c.name,
            slug=c.slug,
            sort_order=c.sort_order,
            is_active=c.is_active,
            children=[],
        )
        for c in rows
        if c.is_active
    }
    roots: list[CategoryNode] = []
    for c in rows:
        if not c.is_active:
            continue
        node = by_id[c.id]
        if c.parent_id is None or c.parent_id not in by_id:
            roots.append(node)
        else:
            by_id[c.parent_id].children.append(node)
    return roots


@router.get("/products", response_model=Page[ProductSummary])
async def list_products(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=120),
    brand: str | None = Query(default=None, max_length=120),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    has_discount: bool = Query(default=False),
) -> Page[ProductSummary]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = ProductRepository(session)
        rows, total = await repo.search(
            offset=params.offset,
            limit=params.limit,
            q=q,
            category_slug=category,
            brand_slug=brand,
            status_filter=ProductStatus.ACTIVE,
            public_only=True,
            min_price=min_price,
            max_price=max_price,
            has_discount=has_discount,
        )
    items = [_product_to_summary(p) for p in rows]
    return Page.build(items=items, total=total, params=params)


@router.get("/video-products")
async def list_video_products(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=200),
    size: int = Query(default=12, ge=1, le=40),
    category: str | None = Query(default=None, max_length=120),
    q: str | None = Query(default=None, max_length=200),
) -> dict:
    """Public feed of products that have at least one approved video.

    Returns a flat shape the customer-web `VideoProductsRow` consumes
    in a single round-trip:

        {
          "items": [
            {
              "product": { ...ProductSummary },
              "video": {
                "id": "<uuid>",
                "hls_url": "https://...m3u8",
                "thumbnail_url": "https://...jpg",
                "duration_seconds": 22
              }
            },
            ...
          ],
          "total": <int>,
          "page": <int>,
          "size": <int>
        }

    Importantly: we filter by `EXISTS approved video`, then for each
    product surface its newest-approved video. Approved-only is
    enforced at the SQL level so unauthorised drafts can't leak.
    """
    from sqlalchemy import and_, exists, func, or_, select
    from sqlalchemy.orm import selectinload

    # Local import keeps catalog module free of a hard dep on
    # product_videos at import-time (matches the pattern other
    # cross-module endpoints in this file already use).
    from app.modules.catalog.models import Category  # noqa: WPS433
    from app.modules.product_videos.models import ProductVideo  # noqa: WPS433

    params = PageParams(page=page, size=size)

    async with uow.transactional() as session:
        # 1) Count + page the products that own ≥1 approved video.
        approved_exists = exists(
            select(ProductVideo.id).where(
                and_(
                    ProductVideo.product_id == Product.id,
                    ProductVideo.status == "approved",
                ),
            ),
        )
        where_clauses = [
            Product.status == ProductStatus.ACTIVE,
            Product.blocked_at.is_(None),
            approved_exists,
        ]
        # Optional category filter (matches the pill bar on /deals).
        if category:
            cat_exists = exists(
                select(Category.id).where(
                    Category.id == Product.category_id,
                    Category.slug == category,
                ),
            )
            where_clauses.append(cat_exists)
        # Optional free-text filter — matches name / search_text.
        if q:
            like = f"%{q.lower()}%"
            where_clauses.append(
                or_(
                    func.lower(Product.name).like(like),
                    func.lower(Product.search_text).like(like),
                ),
            )
        total_stmt = (
            select(func.count())
            .select_from(Product)
            .where(*where_clauses)
        )
        total = int((await session.execute(total_stmt)).scalar_one() or 0)

        list_stmt = (
            select(Product)
            .where(*where_clauses)
            .options(
                selectinload(Product.variants),
                selectinload(Product.media),
                selectinload(Product.brand),
                selectinload(Product.category),
            )
            .order_by(Product.created_at.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        products = (await session.execute(list_stmt)).scalars().unique().all()

        if not products:
            return {
                "items": [],
                "total": total,
                "page": params.page,
                "size": params.size,
            }

        # 2) For each product, fetch its newest approved video in one
        # batched query (no n+1).
        product_ids = [p.id for p in products]
        videos_stmt = (
            select(ProductVideo)
            .where(
                ProductVideo.product_id.in_(product_ids),
                ProductVideo.status == "approved",
            )
            .order_by(
                ProductVideo.product_id,
                ProductVideo.approved_at.desc().nulls_last(),
                ProductVideo.created_at.desc(),
            )
        )
        all_vids = (await session.execute(videos_stmt)).scalars().all()
        first_video_by_product: dict = {}
        for v in all_vids:
            first_video_by_product.setdefault(v.product_id, v)

    items = []
    for p in products:
        v = first_video_by_product.get(p.id)
        if v is None or not v.hls_url:
            # An EXISTS hit with no HLS url means processing finished
            # but the URL field is empty — skip rather than serving a
            # broken player.
            continue
        items.append(
            {
                "product": _product_to_summary(p).model_dump(mode="json"),
                "video": {
                    "id": str(v.id),
                    "hls_url": v.hls_url,
                    "thumbnail_url": v.thumbnail_url,
                    "duration_seconds": v.duration_seconds,
                    "title": v.title,
                },
            },
        )

    return {
        "items": items,
        "total": total,
        "page": params.page,
        "size": params.size,
    }


@router.get("/products/{slug}", response_model=ProductResponse)
async def get_product(
    slug: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ProductResponse:
    async with uow.transactional() as session:
        repo = ProductRepository(session)
        p = await repo.get_publicly_visible_by_slug(slug)
        if p is None:
            raise NotFoundError("Product not found.")
        return _product_to_response(p)


# These remain "public" but only return active records. Admin-only versions
# (which include drafts/archived) live in admin.py.
_ = UUID  # keep import live for future product-by-id endpoint
