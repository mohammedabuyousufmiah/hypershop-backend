"""Admin write endpoints under /api/v1/admin/seo/*.

Permissions: ``seo.configure`` (short-form perm added 2026-05-16).
Held by admin / manager / marketing_manager. The previous
``catalog.write`` gate was a typo — that perm string did NOT exist
in the canonical catalog (it's ``catalog.product.write``), so only
super_admin's wildcard satisfied it.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.audit import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.catalog.models import Product
from app.modules.seo.models import SeoMetaOverride, SeoMetaTranslation
from app.modules.seo.api.public import (
    _banner_to_response,
    _blog_to_response,
)
from app.modules.seo.errors import BlogPostNotFoundError
from app.modules.seo.repository import (
    BlogPostRepository,
    HomepageBannerRepository,
    SeoMetaOverrideRepository,
    UrlRedirectRepository,
)
from app.modules.seo.schemas import (
    BannerCreateRequest,
    BannerListResponse,
    BannerResponse,
    BannerUpdateRequest,
    FaqCreateRequest,
    FaqListResponse,
    FaqResponse,
    FaqUpdateRequest,
    FaqBulkImportRequest,
    FaqBulkImportResponse,
    FaqSuggestRequest,
    FaqSuggestResponse,
    FaqSuggestItem,
    BlogPostCreateRequest,
    BlogPostListItem,
    BlogPostListResponse,
    BlogPostResponse,
    BlogPostUpdateRequest,
    RedirectCreateRequest,
    RedirectListResponse,
    RedirectResponse,
    SeoOverrideResponse,
    SeoOverrideUpsertRequest,
    SeoTranslationListResponse,
    SeoTranslationResponse,
    SeoTranslationUpsertRequest,
)
from app.modules.seo.service import SeoAdminService

router = APIRouter(prefix="/admin/seo", tags=["admin-seo"])

_WRITE = "seo.configure"  # was "catalog.write" (non-existent perm — only super_admin wildcard satisfied it). Rewired 2026-05-16 to the seo.configure short-form perm scoped to admin / manager / marketing_manager.
_READ = "seo.view"  # added with the audit dashboard so support / data roles can read findings without write access.


# ============================================================
#  Auto-SEO engine triggers (ported from v7 — 2026-05-24)
# ============================================================
@router.post(
    "/autogen/product/{product_id}",
    summary="Generate/refresh auto SEO for one product (force overrides engine-owned rows)",
)
async def autogen_product(
    product_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
    force: bool = False,
) -> dict[str, object]:
    """Manual one-shot autogen for a product. ``force=true`` overrides
    a human-curated row (use sparingly — usually you want auto SEO to
    yield to manual edits)."""
    from app.modules.catalog.repository import ProductRepository
    from app.modules.seo.autogen import SeoAutoGenService
    async with uow.transactional() as session:
        product = await ProductRepository(session).get(product_id)
        if product is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Product not found")
        wrote = await SeoAutoGenService(session).generate_for_product(
            product, force=force,
        )
        from app.core.audit import record_audit
        await record_audit(
            actor=principal,
            action="seo.autogen.product",
            resource_type="product",
            resource_id=product_id,
            metadata={"wrote": wrote, "force": force},
        )
    return {"product_id": str(product_id), "wrote": wrote, "force": force}


@router.get(
    "/autogen/stats",
    summary="Keyword-bank size + coverage stats for the auto-SEO engine",
)
async def autogen_stats(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_READ))],
) -> dict[str, object]:
    """Returns the BD purchase-intent keyword-bank pool size + how many
    products currently carry engine-owned SEO meta. Used by the admin
    SEO audit dashboard to show coverage health at a glance."""
    from sqlalchemy import func as sa_func, select as sa_select
    from app.modules.seo.keyword_bank import (
        total_phrase_pool, BUY_INTENT_PREFIX_EN, BUY_INTENT_PREFIX_BN,
        CATEGORY_SEED, BRAND_SEED, LOCATION_MODIFIER,
        SEASONAL_KEYWORD, PRICE_MODIFIER, DELIVERY_MODIFIER,
        PAYMENT_MODIFIER, QUESTION_TEMPLATE_EN, QUESTION_TEMPLATE_BN,
        BANGLISH_PHRASE,
    )
    async with uow.transactional() as session:
        auto_overrides = (
            await session.execute(
                sa_select(sa_func.count(SeoMetaOverride.id)).where(
                    SeoMetaOverride.auto_generated.is_(True),
                ),
            )
        ).scalar_one()
        auto_translations = (
            await session.execute(
                sa_select(sa_func.count(SeoMetaTranslation.id)).where(
                    SeoMetaTranslation.auto_generated.is_(True),
                ),
            )
        ).scalar_one()
        active_products = (
            await session.execute(
                sa_select(sa_func.count(Product.id)).where(
                    Product.status == "active",
                ),
            )
        ).scalar_one()
    return {
        "keyword_pool_size": total_phrase_pool(),
        "bank_components": {
            "intent_prefixes_en": len(BUY_INTENT_PREFIX_EN),
            "intent_prefixes_bn": len(BUY_INTENT_PREFIX_BN),
            "price_modifiers": len(PRICE_MODIFIER),
            "delivery_modifiers": len(DELIVERY_MODIFIER),
            "location_modifiers": len(LOCATION_MODIFIER),
            "payment_modifiers": len(PAYMENT_MODIFIER),
            "seasonal_keywords": len(SEASONAL_KEYWORD),
            "category_seeds": len(CATEGORY_SEED),
            "brand_seeds": len(BRAND_SEED),
            "question_templates": len(QUESTION_TEMPLATE_EN) + len(QUESTION_TEMPLATE_BN),
            "banglish_phrases": len(BANGLISH_PHRASE),
        },
        "products_active": int(active_products),
        "auto_overrides": int(auto_overrides),
        "auto_translations": int(auto_translations),
        "coverage_pct": (
            round(int(auto_overrides) * 100 / int(active_products), 1)
            if active_products else 0.0
        ),
    }


@router.post(
    "/autogen/regenerate",
    summary="Force-regenerate auto SEO for all engine-owned rows (refreshes after keyword-bank update)",
)
async def autogen_regenerate(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
    limit: int = 500,
) -> dict[str, int]:
    """Re-run the engine over every active product, including those
    that already have an engine-owned override. Used after editing
    keyword_bank.py to push refreshed phrases into the existing meta
    rows. Manual-edited rows (auto_generated=False) are still skipped."""
    from app.modules.seo.autogen import SeoAutoGenService
    from sqlalchemy import select as sa_select
    written = 0
    skipped = 0
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                sa_select(Product)
                .where(Product.status == "active")
                .limit(limit),
            )
        ).scalars().all()
        svc = SeoAutoGenService(session)
        for p in rows:
            if await svc.generate_for_product(p, force=False):
                written += 1
            else:
                skipped += 1
        await record_audit(
            actor=principal,
            action="seo.autogen.regenerate",
            resource_type="seo",
            resource_id="*",
            metadata={"scanned": len(rows), "written": written, "skipped": skipped},
        )
    return {"scanned": len(rows), "written": written, "skipped": skipped}


@router.post(
    "/autogen/backfill",
    summary="Bulk generate auto SEO for active products with no override yet",
)
async def autogen_backfill(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
    limit: int = 500,
) -> dict[str, int]:
    """Progressive backfill — each call scans up to ``limit`` active
    products that still don't have any SEO override and generates auto
    metadata for them. Idempotent: rerun until ``scanned == 0``."""
    from app.modules.seo.autogen import SeoAutoGenService
    async with uow.transactional() as session:
        result = await SeoAutoGenService(session).backfill_products(
            limit=limit,
        )
        from app.core.audit import record_audit
        await record_audit(
            actor=principal,
            action="seo.autogen.backfill",
            resource_type="seo",
            resource_id="*",
            metadata=result,
        )
    return result


@router.get(
    "/audit",
    summary="SEO audit — products missing structured-data fields",
)
async def seo_audit(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[
        Principal, Depends(requires_permission(_READ)),
    ],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """Scan active products + return rows missing high-value SEO
    attributes that the storefront productJsonLd would otherwise emit
    as undefined: ``gtin13/12/8``, ``mpn``, ``material``, ``color``,
    ``size``, ``warranty_months``, ``energy_class``, ``image_width``.

    Each row has the product id + slug + name + a list of ``missing``
    keys so the admin UI can group + render one-click fix CTAs.

    Defaults to checking ACTIVE products only (the only ones whose
    PDP actually ships JSON-LD to crawlers). The endpoint is paginated
    because a 100k-SKU catalog would otherwise OOM the response.
    """
    from sqlalchemy import select as sa_select, func as sa_func
    from app.modules.catalog.models import Product
    from app.modules.catalog.models import ProductMedia

    AUDITED_KEYS = (
        "gtin13", "gtin12", "gtin8", "mpn",
        "material", "color", "size",
        "warranty_months", "energy_class", "image_width",
    )

    async with uow.transactional() as session:
        # Paginated active-product slice — only emits the row when at
        # least one audited key is missing. Doing the filter in Python
        # keeps the query portable across PG/MySQL; the audit panel is
        # admin-only + low-QPS so the row-by-row pass is fine.
        rows = (
            await session.execute(
                sa_select(
                    Product.id,
                    Product.slug,
                    Product.name,
                    Product.attributes,
                    Product.category_id,
                )
                .where(Product.status == "active")
                .order_by(Product.updated_at.desc())
                .offset(offset)
                .limit(limit),
            )
        ).all()
        total = (
            await session.execute(
                sa_select(sa_func.count(Product.id))
                .where(Product.status == "active"),
            )
        ).scalar_one()

        # Image-dimension audit needs a second query — admin can
        # backfill via the catalog editor with one click.
        image_dims_present: set[str] = set()
        if rows:
            dim_rows = (
                await session.execute(
                    sa_select(ProductMedia.product_id)
                    .where(
                        ProductMedia.product_id.in_([r[0] for r in rows]),
                        ProductMedia.width.is_not(None),
                        ProductMedia.height.is_not(None),
                    )
                    .distinct(),
                )
            ).all()
            image_dims_present = {str(p[0]) for p in dim_rows}

        items: list[dict[str, object]] = []
        for pid, slug, name, attrs, _cat_id in rows:
            attrs = attrs or {}
            missing: list[str] = []
            # Either-or check: any GTIN variant satisfies, since
            # Google accepts gtin13/12/8 interchangeably for product
            # identifier disambiguation.
            has_any_gtin = any(
                str(attrs.get(k, "")).strip()
                for k in ("gtin13", "gtin12", "gtin8", "gtin")
            )
            if not has_any_gtin:
                missing.append("gtin")
            for k in ("mpn", "material", "color", "size",
                     "warranty_months", "energy_class"):
                if not str(attrs.get(k, "")).strip():
                    missing.append(k)
            if str(pid) not in image_dims_present:
                missing.append("image_dimensions")
            if missing:
                items.append({
                    "id": str(pid),
                    "slug": slug,
                    "name": name,
                    "missing": missing,
                    "edit_url": f"/admin/catalog/products/{pid}",
                })

        # Roll-up — missing-by-key counts let the UI pick the biggest
        # backlog to fix first.
        by_key: dict[str, int] = {}
        for it in items:
            for k in it["missing"]:  # type: ignore[union-attr]
                by_key[k] = by_key.get(k, 0) + 1

        return {
            "total_active": int(total),
            "scanned": len(rows),
            "missing_count": len(items),
            "missing_by_key": dict(sorted(
                by_key.items(), key=lambda x: -x[1],
            )),
            "items": items,
            "audited_keys": list(AUDITED_KEYS),
            "limit": limit,
            "offset": offset,
        }


# ----------------------------------------------------------------------
# Overrides
# ----------------------------------------------------------------------
@router.put(
    "/overrides",
    response_model=SeoOverrideResponse,
    summary="Upsert a per-(entity_type × entity_key) SEO override",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def upsert_override(
    body: SeoOverrideUpsertRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SeoOverrideResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.upsert_override(
            entity_type=body.entity_type,
            entity_key=body.entity_key,
            principal=principal,
            title=body.title,
            meta_description=body.meta_description,
            canonical_url=body.canonical_url,
            og_image_url=body.og_image_url,
            og_type=body.og_type,
            twitter_card=body.twitter_card,
            robots_directives=body.robots_directives,
            extra_meta_json=body.extra_meta_json,
            extra_jsonld_json=body.extra_jsonld_json,
        )
    return _override_to_response(row)


@router.delete(
    "/overrides/{override_id}",
    status_code=204,
    response_model=None,
    summary="Delete an SEO override (entity falls back to auto-defaults)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def delete_override(
    override_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        await svc.delete_override(
            override_id=override_id, principal=principal,
        )


# ----------------------------------------------------------------------
# Translations  — Module 34 multi-language sprint
# ----------------------------------------------------------------------
@router.put(
    "/translations",
    response_model=SeoTranslationResponse,
    summary="Upsert a per-(entity_type × entity_key × locale) SEO translation",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def upsert_translation(
    body: SeoTranslationUpsertRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SeoTranslationResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.upsert_translation(
            entity_type=body.entity_type,
            entity_key=body.entity_key,
            locale=body.locale,
            principal=principal,
            title=body.title,
            meta_description=body.meta_description,
            og_title=body.og_title,
            og_description=body.og_description,
            keywords=body.keywords,
        )
    return _translation_to_response(row)


@router.get(
    "/translations",
    response_model=SeoTranslationListResponse,
    summary="List SEO translations for an entity (one row per locale)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def list_translations(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    entity_type: Annotated[str, Query(min_length=1, max_length=24)],
    entity_key: Annotated[str, Query(min_length=1, max_length=160)],
) -> SeoTranslationListResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        rows = await svc.list_translations(
            entity_type=entity_type, entity_key=entity_key,
        )
    return SeoTranslationListResponse(
        items=[_translation_to_response(r) for r in rows],
    )


@router.delete(
    "/translations/{translation_id}",
    status_code=204,
    response_model=None,
    summary="Delete a translation row (entity falls back to default locale)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def delete_translation(
    translation_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        await svc.delete_translation(
            translation_id=translation_id, principal=principal,
        )


def _translation_to_response(r) -> SeoTranslationResponse:
    return SeoTranslationResponse(
        id=r.id,
        entity_type=r.entity_type,
        entity_key=r.entity_key,
        locale=r.locale,
        title=r.title,
        meta_description=r.meta_description,
        og_title=r.og_title,
        og_description=r.og_description,
        keywords=r.keywords,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


# ----------------------------------------------------------------------
# Banners
# ----------------------------------------------------------------------
@router.get(
    "/banners",
    response_model=BannerListResponse,
    summary="List ALL banners (active + inactive + scheduled)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def list_all_banners(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> BannerListResponse:
    async with uow.transactional() as session:
        repo = HomepageBannerRepository(session)
        rows = await repo.list_all(limit=limit)
    return BannerListResponse(items=[_banner_to_response(r) for r in rows])


@router.post(
    "/banners",
    response_model=BannerResponse,
    status_code=201,
    summary="Create a homepage banner",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def create_banner(
    body: BannerCreateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BannerResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.create_banner(
            principal=principal,
            **body.model_dump(),
        )
    return _banner_to_response(row)


@router.patch(
    "/banners/{banner_id}",
    response_model=BannerResponse,
    summary="Update a banner",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def update_banner(
    banner_id: UUID,
    body: BannerUpdateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BannerResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.update_banner(
            banner_id=banner_id,
            principal=principal,
            **body.model_dump(exclude_unset=True),
        )
    return _banner_to_response(row)


@router.post(
    "/banners/{banner_id}/deactivate",
    response_model=BannerResponse,
    summary="Hide a banner from the public list (keeps history)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def deactivate_banner(
    banner_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BannerResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.deactivate_banner(
            banner_id=banner_id, principal=principal,
        )
    return _banner_to_response(row)


# ----------------------------------------------------------------------
# Blog posts
# ----------------------------------------------------------------------
@router.get(
    "/blog",
    response_model=BlogPostListResponse,
    summary="List ALL blog posts (any status)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def list_all_blog(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> BlogPostListResponse:
    async with uow.transactional() as session:
        repo = BlogPostRepository(session)
        rows = await repo.list_all(limit=limit)
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


@router.post(
    "/blog",
    response_model=BlogPostResponse,
    status_code=201,
    summary="Create a blog post (defaults to draft)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def create_blog_post(
    body: BlogPostCreateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BlogPostResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.create_blog_post(
            principal=principal,
            slug=body.slug,
            title=body.title,
            excerpt=body.excerpt,
            body_markdown=body.body_markdown,
            cover_image_url=body.cover_image_url,
            author_name=body.author_name,
            status=body.status,
            tags_csv=body.tags_csv,
        )
    return _blog_to_response(row)


@router.patch(
    "/blog/{post_id}",
    response_model=BlogPostResponse,
    summary="Update a blog post",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def update_blog_post(
    post_id: UUID,
    body: BlogPostUpdateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BlogPostResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.update_blog_post(
            post_id=post_id, principal=principal,
            **body.model_dump(exclude_unset=True),
        )
    return _blog_to_response(row)


@router.post(
    "/blog/{post_id}/publish",
    response_model=BlogPostResponse,
    summary="Publish a draft blog post (sets published_at = now)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def publish_blog_post(
    post_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BlogPostResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.publish_blog_post(
            post_id=post_id, principal=principal,
        )
    return _blog_to_response(row)


@router.post(
    "/blog/{post_id}/archive",
    response_model=BlogPostResponse,
    summary="Archive a blog post (removed from public listings + sitemap)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def archive_blog_post(
    post_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BlogPostResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.archive_blog_post(
            post_id=post_id, principal=principal,
        )
    return _blog_to_response(row)


# ----------------------------------------------------------------------
# Redirects
# ----------------------------------------------------------------------
@router.get(
    "/redirects",
    response_model=RedirectListResponse,
    summary="List URL redirects",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def list_redirects(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> RedirectListResponse:
    async with uow.transactional() as session:
        repo = UrlRedirectRepository(session)
        rows = await repo.list_all(limit=limit)
    return RedirectListResponse(items=[
        _redirect_to_response(r) for r in rows
    ])


@router.post(
    "/redirects",
    response_model=RedirectResponse,
    status_code=201,
    summary="Create a 301/302 URL redirect (served at /r/<from_path>)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def create_redirect(
    body: RedirectCreateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RedirectResponse:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        row = await svc.create_redirect(
            from_path=body.from_path,
            to_path=body.to_path,
            redirect_type=body.redirect_type,
            principal=principal,
            note=body.note,
        )
    return _redirect_to_response(row)


@router.delete(
    "/redirects/{redirect_id}",
    status_code=204,
    response_model=None,
    summary="Delete a redirect",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def delete_redirect(
    redirect_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = SeoAdminService(session)
        await svc.delete_redirect(
            redirect_id=redirect_id, principal=principal,
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _override_to_response(r) -> SeoOverrideResponse:
    return SeoOverrideResponse(
        id=r.id,
        entity_type=r.entity_type,
        entity_key=r.entity_key,
        title=r.title,
        meta_description=r.meta_description,
        canonical_url=r.canonical_url,
        og_image_url=r.og_image_url,
        og_type=r.og_type,
        twitter_card=r.twitter_card,
        robots_directives=r.robots_directives,
        extra_meta=r.extra_meta_json,
        extra_jsonld=r.extra_jsonld_json,
    )


def _redirect_to_response(r) -> RedirectResponse:
    return RedirectResponse(
        id=r.id,
        from_path=r.from_path,
        to_path=r.to_path,
        redirect_type=r.redirect_type,
        is_active=r.is_active,
        last_hit_at=r.last_hit_at,
        hit_count=r.hit_count,
        note=r.note,
    )


# ----------------------------------------------------------------------
# Product FAQs  — drive FAQPage JSON-LD on the product page
# ----------------------------------------------------------------------
def _faq_to_response(r) -> FaqResponse:
    return FaqResponse(
        id=r.id,
        entity_type=r.entity_type,
        entity_key=r.entity_key,
        locale=r.locale,
        question=r.question,
        answer=r.answer,
        position=r.position,
        is_active=r.is_active,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.get(
    "/faqs",
    response_model=FaqListResponse,
    summary="List ALL FAQs for an entity (any locale / status)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def list_entity_faqs(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    entity_type: Annotated[str, Query(max_length=24)],
    entity_key: Annotated[str, Query(min_length=1, max_length=160)],
) -> FaqListResponse:
    async with uow.transactional() as session:
        rows = await SeoAdminService(session).list_faqs(
            entity_type=entity_type, entity_key=entity_key,
        )
    return FaqListResponse(items=[_faq_to_response(r) for r in rows])


@router.post(
    "/faqs",
    response_model=FaqResponse,
    status_code=201,
    summary="Add an FAQ to an entity (product / category / brand)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def create_entity_faq(
    body: FaqCreateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> FaqResponse:
    async with uow.transactional() as session:
        row = await SeoAdminService(session).create_faq(
            principal=principal,
            **body.model_dump(),
        )
    return _faq_to_response(row)


@router.patch(
    "/faqs/{faq_id}",
    response_model=FaqResponse,
    summary="Update an FAQ",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def update_product_faq(
    faq_id: UUID,
    body: FaqUpdateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> FaqResponse:
    async with uow.transactional() as session:
        row = await SeoAdminService(session).update_faq(
            faq_id=faq_id, principal=principal,
            **body.model_dump(exclude_unset=True),
        )
    return _faq_to_response(row)


@router.delete(
    "/faqs/{faq_id}",
    status_code=204,
    response_model=None,
    summary="Delete an FAQ",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def delete_product_faq(
    faq_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        await SeoAdminService(session).delete_faq(
            faq_id=faq_id, principal=principal,
        )


# ============================================================
#  FAQ bulk-import + AI-suggest (task #169, 2026-05-25)
# ============================================================
@router.post(
    "/faqs/bulk",
    response_model=FaqBulkImportResponse,
    summary="Bulk-create up to 500 FAQs in one shot (CSV import flow)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def bulk_create_faqs(
    body: FaqBulkImportRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> FaqBulkImportResponse:
    """Used by the admin FAQ tab's CSV-paste / file-upload flow. Each
    row still routes through the per-row audit trail so the batch can
    be replayed from the audit log alone."""
    async with uow.transactional() as session:
        summary = await SeoAdminService(session).bulk_create_faqs(
            items=[i.model_dump() for i in body.items],
            principal=principal,
            skip_duplicates=body.skip_duplicates,
        )
    return FaqBulkImportResponse(**summary)


@router.post(
    "/faqs/suggest",
    response_model=FaqSuggestResponse,
    summary="Generate FAQ candidates for an entity (template-based; LLM swap when bound)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def suggest_faqs(
    body: FaqSuggestRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
) -> FaqSuggestResponse:
    """Returns 5-12 question/answer candidates per entity. The admin
    UI surfaces these as checkboxes; the operator picks rows and posts
    them through /faqs/bulk. Source today is always "template" — the
    LLM path is wired but soft-fails until a transport is configured."""
    from app.modules.seo.faq_suggester import suggest_faqs as _suggest
    async with uow.transactional() as session:
        items, source, notice = await _suggest(
            session,
            entity_type=body.entity_type,
            entity_key=body.entity_key,
            locale=body.locale,
            max_items=body.max_items,
        )
    return FaqSuggestResponse(
        items=[
            FaqSuggestItem(question=q, answer=a, source=s)
            for (q, a, s) in items
        ],
        source=source,
        notice=notice,
    )


# ============================================================
#  Sitemap shard status (task #170, 2026-05-25)
# ============================================================
@router.get(
    "/sitemap/shards",
    summary="Per-kind sitemap shard health: URL count, shard count, fill%",
)
async def sitemap_shard_status(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_READ))],
) -> dict[str, object]:
    """Surfaces how close each sitemap kind is to needing another shard.
    A high ``last_shard_fill_pct`` (>=80) on any kind is the early
    warning to plan a re-shard, sub-category split, or a cap tune
    before the catalog scales into the 50,000-URL hard ceiling."""
    from app.modules.seo.service import SitemapService
    async with uow.transactional() as session:
        return await SitemapService(session).shard_status()


# ============================================================
#  IndexNow runtime status (task #168, 2026-05-25)
# ============================================================
@router.get(
    "/indexnow/status",
    summary="IndexNow queue depth, last-publish, last-ping timestamps + lag",
)
async def indexnow_status(
    _p: Annotated[Principal, Depends(requires_permission(_READ))],
) -> dict[str, object]:
    """Live ops dial for the IndexNow submission pipeline. Reports:

    - queue_depth / queue_cap / queue_fill_pct
    - last_enqueue_at + last_enqueue_url (most recent publish hook fire)
    - last_ping_at + last_ping_status + last_ping_url_count (drain cron)
    - last_ping_error (last exception string, or null)
    - lag_seconds (last_enqueue minus last_ping; how stale the queue is)
    - ping_total / ping_success_total / enqueue_total / drop_total

    All counters reset on process restart. Use this from /admin/seo-audit
    to detect a stuck ping job (lag growing without ping_total climbing)
    or a misconfigured key (last_ping_status == 403 sustained).
    """
    from app.modules.seo.jobs import get_indexnow_metrics
    from app.core.config import get_settings as _s
    s = _s()
    metrics = get_indexnow_metrics()
    metrics["config"] = {
        "enabled": bool(getattr(s, "seo_indexnow_enabled", False)),
        "key_set": bool((getattr(s, "seo_indexnow_key", "") or "").strip()),
        "site_url": getattr(s, "seo_site_url", "") or "",
    }
    return metrics
