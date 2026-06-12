"""Public read endpoints under /api/v1/storefront/*.

Two endpoints today:

- ``GET /storefront/layout`` — unified layout payload (banners + nav +
  featured categories + footer pages). One round-trip serves the
  storefront's chrome.
- ``GET /storefront/pages/{slug}`` — single static page render.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.seo.repository import HomepageBannerRepository
from app.modules.storefront_cms.repository import (
    FeaturedCategoryRepository,
    NavItemRepository,
    StaticPageRepository,
)
from app.modules.storefront_cms.revalidate import current_version
from app.modules.storefront_cms.schemas import (
    FeaturedCategoryResponse,
    NavItemResponse,
    StaticPageListItem,
    StaticPageResponse,
    StorefrontBanner,
    StorefrontLayoutResponse,
)

router = APIRouter(prefix="/storefront", tags=["storefront"])


@router.get(
    "/layout",
    response_model=StorefrontLayoutResponse,
    summary="Unified storefront chrome — banners + nav + featured + footer pages",
)
async def storefront_layout(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> StorefrontLayoutResponse:
    async with uow.transactional() as session:
        banners_repo = HomepageBannerRepository(session)
        nav_repo = NavItemRepository(session)
        feat_repo = FeaturedCategoryRepository(session)
        pages_repo = StaticPageRepository(session)
        # Parallel-ish; transactional() shares one session so we
        # serialise but each query is sub-ms on small tables.
        banners = await banners_repo.list_active_now(limit=20)
        nav_items = await nav_repo.list_active()
        featured = await feat_repo.list_active()
        pages = [p for p in await pages_repo.list_published() if p.show_in_footer]

    return StorefrontLayoutResponse(
        version=current_version(),
        banners=[
            StorefrontBanner(
                id=b.id,
                title=b.title,
                subtitle=b.subtitle,
                image_url=b.image_url,
                mobile_image_url=b.mobile_image_url,
                target_url=b.target_url,
                alt_text=b.alt_text,
                sort_order=b.sort_order,
            ) for b in banners
        ],
        nav_items=[NavItemResponse.model_validate(n) for n in nav_items],
        featured_categories=[
            FeaturedCategoryResponse.model_validate(f) for f in featured
        ],
        footer_pages=[StaticPageListItem.model_validate(p) for p in pages],
    )


@router.get(
    "/pages/{slug}",
    response_model=StaticPageResponse,
    summary="Render a single static page by slug",
)
async def get_static_page(
    slug: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> StaticPageResponse:
    async with uow.transactional() as session:
        page = await StaticPageRepository(session).get_by_slug(slug)
    if page is None or not page.is_published:
        raise HTTPException(status_code=404, detail="Page not found")
    return StaticPageResponse.model_validate(page)
