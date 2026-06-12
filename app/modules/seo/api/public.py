"""Public JSON read endpoints under /api/v1/seo/*.

The frontend calls these on every page render and inserts the result
into the document ``<head>``. Cached aggressively by an upstream CDN
since they're public + idempotent.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.seo.builders import organization_jsonld
from app.modules.seo.repository import (
    BlogPostRepository,
    HomepageBannerRepository,
)
from app.modules.seo.schemas import (
    BannerListResponse,
    BannerResponse,
    BlogPostListItem,
    BlogPostListResponse,
    BlogPostResponse,
    CommercePolicyResponse,
    SeoBundleResponse,
    SiteConfigResponse,
)
from app.modules.seo.service import (
    SeoBundleService,
    site_context_from_settings,
)
from app.modules.seo.state import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    normalise_locale,
)

router = APIRouter(prefix="/seo", tags=["seo"])


# Reused on every /meta/* endpoint — keeps the locale contract one-liner.
# Default lives in the `= DEFAULT_LOCALE` of each handler signature; setting
# it here too is rejected by newer FastAPI as an Annotated-default conflict.
_LANG_QUERY = Query(
    description=(
        f"Locale for the SEO bundle. Supported: "
        f"{', '.join(SUPPORTED_LOCALES)}. Unknown values fall back to "
        f"'{DEFAULT_LOCALE}'."
    ),
    alias="lang",
    max_length=8,
)


# ----------------------------------------------------------------------
# Site config (read-only)
# ----------------------------------------------------------------------
@router.get(
    "/site-config",
    response_model=SiteConfigResponse,
    summary="Public site config — name, URL, default OG image, organization JSON-LD",
)
async def site_config(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> SiteConfigResponse:
    from app.core.config import get_settings
    from sqlalchemy import select as sa_select
    ctx = site_context_from_settings()
    s = get_settings()

    # COD districts — pulled from the delivery_zones table so the SEO
    # eligibleRegion stays in sync with the operational reality. Empty
    # list = COD is country-wide (or no zones configured).
    cod_districts: list[str] = []
    try:
        from app.modules.delivery.models import DeliveryZone  # type: ignore
        async with uow.transactional() as session:
            rows = (
                await session.execute(
                    sa_select(DeliveryZone.cities)
                    .where(
                        DeliveryZone.is_active.is_(True),
                        DeliveryZone.kind == "service_area",
                    ),
                )
            ).all()
            seen: set[str] = set()
            for (cities,) in rows:
                for c in (cities or []):
                    if c and c not in seen:
                        seen.add(c)
                        cod_districts.append(c)
    except Exception:  # noqa: BLE001
        # Delivery module not loaded / table not migrated — ship empty
        # and let the FE fall back to "no eligibleRegion" (country-wide).
        cod_districts = []

    commerce = CommercePolicyResponse(
        return_days=getattr(s, "seo_return_days", 7),
        return_fees=getattr(s, "seo_return_fees", "FreeReturn"),
        return_method=getattr(s, "seo_return_method", "ReturnByMail"),
        shipping_flat_minor=getattr(s, "seo_shipping_flat_minor", 6000),
        shipping_free_threshold_minor=getattr(
            s, "seo_shipping_free_threshold_minor", 99900,
        ),
        shipping_handling_min_days=getattr(
            s, "seo_shipping_handling_min_days", 0,
        ),
        shipping_handling_max_days=getattr(
            s, "seo_shipping_handling_max_days", 1,
        ),
        shipping_transit_min_days=getattr(
            s, "seo_shipping_transit_min_days", 1,
        ),
        shipping_transit_max_days=getattr(
            s, "seo_shipping_transit_max_days", 3,
        ),
        shipping_outside_flat_minor=getattr(
            s, "seo_shipping_outside_flat_minor", 12000,
        ),
        shipping_outside_free_threshold_minor=getattr(
            s, "seo_shipping_outside_free_threshold_minor", 199900,
        ),
        shipping_outside_transit_min_days=getattr(
            s, "seo_shipping_outside_transit_min_days", 3,
        ),
        shipping_outside_transit_max_days=getattr(
            s, "seo_shipping_outside_transit_max_days", 7,
        ),
        shipping_dhaka_region=getattr(s, "seo_shipping_dhaka_region", "Dhaka"),
        cod_eligible_districts=cod_districts,
        country_code=ctx.pharmacy_country or "BD",
        currency="BDT",
        seasonal_name=getattr(s, "seo_return_seasonal_name", "") or "",
        seasonal_start=getattr(s, "seo_return_seasonal_start", "") or "",
        seasonal_end=getattr(s, "seo_return_seasonal_end", "") or "",
        seasonal_days=getattr(s, "seo_return_seasonal_days", 0) or 0,
    )
    return SiteConfigResponse(
        site_name=ctx.site_name,
        site_url=ctx.site_url,
        default_og_image=ctx.default_og_image,
        pharmacy_phone=ctx.pharmacy_phone,
        pharmacy_locality=ctx.pharmacy_locality,
        pharmacy_country=ctx.pharmacy_country,
        organization_jsonld=organization_jsonld(ctx),
        commerce_policy=commerce,
        verify_google=getattr(s, "seo_verify_google", "") or "",
        verify_bing=getattr(s, "seo_verify_bing", "") or "",
        verify_yandex=getattr(s, "seo_verify_yandex", "") or "",
        verify_naver=getattr(s, "seo_verify_naver", "") or "",
        verify_seznam=getattr(s, "seo_verify_seznam", "") or "",
        verify_facebook=getattr(s, "seo_verify_facebook", "") or "",
        verify_pinterest=getattr(s, "seo_verify_pinterest", "") or "",
    )


# ----------------------------------------------------------------------
# SEO bundle endpoints
# ----------------------------------------------------------------------
@router.get(
    "/meta/home",
    response_model=SeoBundleResponse,
    summary="SEO bundle for the homepage",
)
async def home_meta(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    lang: Annotated[str, _LANG_QUERY] = DEFAULT_LOCALE,
) -> SeoBundleResponse:
    locale = normalise_locale(lang)
    async with uow.transactional() as session:
        bundle = await SeoBundleService(session).for_home(locale=locale)
    return _bundle_to_response(bundle)


@router.get(
    "/meta/product/{product_id}",
    response_model=SeoBundleResponse,
    summary="SEO bundle for a product (with JSON-LD Product schema)",
)
async def product_meta(
    product_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    lang: Annotated[str, _LANG_QUERY] = DEFAULT_LOCALE,
) -> SeoBundleResponse:
    locale = normalise_locale(lang)
    async with uow.transactional() as session:
        bundle = await SeoBundleService(session).for_product(
            product_id, locale=locale,
        )
    return _bundle_to_response(bundle)


@router.get(
    "/meta/category/{category_id}",
    response_model=SeoBundleResponse,
    summary="SEO bundle for a category",
)
async def category_meta(
    category_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    lang: Annotated[str, _LANG_QUERY] = DEFAULT_LOCALE,
) -> SeoBundleResponse:
    locale = normalise_locale(lang)
    async with uow.transactional() as session:
        bundle = await SeoBundleService(session).for_category(
            category_id, locale=locale,
        )
    return _bundle_to_response(bundle)


@router.get(
    "/meta/brand/{brand_id}",
    response_model=SeoBundleResponse,
    summary="SEO bundle for a brand",
)
async def brand_meta(
    brand_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    lang: Annotated[str, _LANG_QUERY] = DEFAULT_LOCALE,
) -> SeoBundleResponse:
    locale = normalise_locale(lang)
    async with uow.transactional() as session:
        bundle = await SeoBundleService(session).for_brand(
            brand_id, locale=locale,
        )
    return _bundle_to_response(bundle)


@router.get(
    "/meta/blog/{slug}",
    response_model=SeoBundleResponse,
    summary="SEO bundle for a published blog post (with BlogPosting schema)",
)
async def blog_post_meta(
    slug: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    lang: Annotated[str, _LANG_QUERY] = DEFAULT_LOCALE,
) -> SeoBundleResponse:
    locale = normalise_locale(lang)
    async with uow.transactional() as session:
        bundle = await SeoBundleService(session).for_blog_post_by_slug(
            slug, locale=locale,
        )
    return _bundle_to_response(bundle)


# ----------------------------------------------------------------------
# Banners (homepage carousel)
# ----------------------------------------------------------------------
@router.get(
    "/banners",
    response_model=BannerListResponse,
    summary="Active homepage banners visible right now",
)
async def list_banners(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> BannerListResponse:
    async with uow.transactional() as session:
        repo = HomepageBannerRepository(session)
        rows = await repo.list_active_now(limit=limit)
    return BannerListResponse(items=[_banner_to_response(r) for r in rows])


# ----------------------------------------------------------------------
# Blog (public listing)
# ----------------------------------------------------------------------
@router.get(
    "/blog",
    response_model=BlogPostListResponse,
    summary="Published blog posts (newest first)",
)
async def list_blog(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> BlogPostListResponse:
    async with uow.transactional() as session:
        repo = BlogPostRepository(session)
        rows = await repo.list_published(limit=limit)
    return BlogPostListResponse(items=[
        BlogPostListItem(
            id=p.id,
            slug=p.slug,
            title=p.title,
            excerpt=p.excerpt,
            cover_image_url=p.cover_image_url,
            author_name=p.author_name,
            status=p.status,
            published_at=p.published_at,
            tags_csv=p.tags_csv,
        )
        for p in rows
    ])


@router.get(
    "/blog/{slug}",
    response_model=BlogPostResponse,
    summary="Read a published blog post by slug",
)
async def get_blog_post(
    slug: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> BlogPostResponse:
    from app.modules.seo.errors import BlogPostNotFoundError
    async with uow.transactional() as session:
        repo = BlogPostRepository(session)
        post = await repo.get_by_slug(slug)
        if post is None or post.status != "published":
            raise BlogPostNotFoundError("Blog post not found.")
    return _blog_to_response(post)


# ----------------------------------------------------------------------
# Helpers (also used by api/admin.py)
# ----------------------------------------------------------------------
def _bundle_to_response(b) -> SeoBundleResponse:
    return SeoBundleResponse(
        title=b.title,
        meta_title=b.meta_title,
        meta_description=b.meta_description,
        canonical_url=b.canonical_url,
        og_type=b.og_type,
        og_image=b.og_image,
        og_url=b.og_url,
        twitter_card=b.twitter_card,
        robots=b.robots,
        extra_meta=b.extra_meta,
        jsonld=b.jsonld,
        locale=getattr(b, "locale", DEFAULT_LOCALE),
        hreflang=getattr(b, "hreflang", []),
    )


def _banner_to_response(r) -> BannerResponse:
    return BannerResponse(
        id=r.id,
        title=r.title,
        subtitle=r.subtitle,
        image_url=r.image_url,
        mobile_image_url=r.mobile_image_url,
        target_url=r.target_url,
        alt_text=r.alt_text,
        is_active=r.is_active,
        sort_order=r.sort_order,
        valid_from=r.valid_from,
        valid_until=r.valid_until,
    )


def _blog_to_response(p) -> BlogPostResponse:
    return BlogPostResponse(
        id=p.id,
        slug=p.slug,
        title=p.title,
        excerpt=p.excerpt,
        body_markdown=p.body_markdown,
        cover_image_url=p.cover_image_url,
        author_name=p.author_name,
        status=p.status,
        published_at=p.published_at,
        tags_csv=p.tags_csv,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )
