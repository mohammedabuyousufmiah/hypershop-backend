"""SEO module orchestration.

Three logical services co-located:

  SeoBundleService          — public read path. Builds the SeoBundle
                              for a given entity, overlays any
                              per-entity override.
  SeoAdminService           — admin write path for overrides + banners
                              + blog + redirects.
  SitemapService            — generates the dynamic sitemap.xml from
                              active catalog rows + published blog +
                              static page list.

The frontend's typical flow:
  1. Render route → fetch ``/api/v1/seo/meta/{entity}/{key}`` → inject
     into ``<head>`` (title, meta tags, JSON-LD).
  2. Crawlers hit ``/sitemap.xml`` + ``/robots.txt`` directly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.catalog.models import Brand, Category, Product
from app.modules.seo import builders, codes
from app.modules.seo.builders import (
    SeoBundle,
    SiteContext,
    apply_override,
    apply_translation,
    hreflang_links,
)
from app.modules.seo.errors import (
    BannerNotFoundError,
    BlogPostNotFoundError,
    BlogSlugTakenError,
    EntityNotFoundError,
    RedirectLoopError,
    RedirectNotFoundError,
    SeoOverrideNotFoundError,
)
from app.modules.seo.models import BlogPost, HomepageBanner
from app.modules.seo.repository import (
    BlogPostRepository,
    EntityFaqRepository,
    HomepageBannerRepository,
    SeoMetaOverrideRepository,
    SeoMetaTranslationRepository,
    UrlRedirectRepository,
)
from app.modules.seo.state import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    BlogPostStatus,
    SeoEntityType,
    normalise_locale,
)

_log = get_logger("hypershop.seo.service")


def site_context_from_settings() -> SiteContext:
    """Build the SiteContext from app settings — used by every endpoint
    so the bundle has consistent site_url/site_name/og_image.
    """
    s = get_settings()
    return SiteContext(
        site_name=getattr(s, "seo_site_name", "Hypershop"),
        site_url=getattr(
            s, "seo_site_url", "https://hypershop.example",
        ).rstrip("/"),
        default_og_image=getattr(s, "seo_default_og_image", None) or None,
        pharmacy_phone=getattr(s, "seo_org_phone", None) or None,
        pharmacy_locality=getattr(s, "seo_org_locality", "Dhaka"),
        pharmacy_country=getattr(s, "seo_org_country", "BD"),
        org_street=getattr(s, "seo_org_street", "") or None,
        org_region=getattr(s, "seo_org_region", "") or None,
        org_postal_code=getattr(s, "seo_org_postal_code", "") or None,
        org_lat=getattr(s, "seo_org_lat", "") or None,
        org_lng=getattr(s, "seo_org_lng", "") or None,
        org_price_range=getattr(s, "seo_org_price_range", "") or None,
        org_opening_hours=getattr(s, "seo_org_opening_hours", "") or None,
        contact_support_phone=getattr(s, "seo_contact_support_phone", "") or None,
        contact_sales_phone=getattr(s, "seo_contact_sales_phone", "") or None,
        contact_billing_phone=getattr(s, "seo_contact_billing_phone", "") or None,
        contact_languages=tuple(
            [c.strip() for c in (
                getattr(s, "seo_contact_languages", "en,bn") or "en,bn"
            ).split(",") if c.strip()]
        ) or ("en", "bn"),
        contact_hours=getattr(s, "seo_contact_hours", "") or None,
        default_locale=DEFAULT_LOCALE,
        supported_locales=SUPPORTED_LOCALES,
    )


# ============================================================
#  SeoBundleService
# ============================================================
class SeoBundleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.overrides = SeoMetaOverrideRepository(session)
        # Module 34 multi-language sprint
        self.translations = SeoMetaTranslationRepository(session)

    # ------------------------------------------------------------------
    # Locale-aware finaliser — applies translation + hreflang on top
    # of the override-overlaid bundle.
    # ------------------------------------------------------------------
    async def _finalise(
        self,
        bundle: SeoBundle,
        *,
        ctx: SiteContext,
        entity_type: str,
        entity_key: str,
        locale: str,
    ) -> SeoBundle:
        # 1. Look up the requested-locale translation (if any).
        translation = None
        if locale != ctx.default_locale:
            translation = await self.translations.get(
                entity_type=entity_type,
                entity_key=entity_key,
                locale=locale,
            )
        # 2. Apply the translation row on top of the bundle.
        bundle = apply_translation(bundle, translation=translation)
        bundle.locale = locale
        # 3. Build hreflang alternates from the available locales.
        available = await self.translations.list_locales_for(
            entity_type=entity_type, entity_key=entity_key,
        )
        bundle.hreflang = hreflang_links(
            canonical_url=bundle.canonical_url,
            default_locale=ctx.default_locale,
            available_locales=available,
        )
        return bundle

    # ------------------------------------------------------------------
    # Per-entity bundle endpoints
    # ------------------------------------------------------------------
    async def for_home(self, *, locale: str = DEFAULT_LOCALE) -> SeoBundle:
        locale = normalise_locale(locale)
        ctx = site_context_from_settings()
        bundle = builders.build_home_meta(ctx=ctx)
        override = await self.overrides.get(
            entity_type=SeoEntityType.STATIC_PAGE.value,
            entity_key="home",
        )
        bundle = apply_override(bundle, override=override)
        return await self._finalise(
            bundle, ctx=ctx,
            entity_type=SeoEntityType.STATIC_PAGE.value,
            entity_key="home",
            locale=locale,
        )

    async def _faqs_for(
        self, *, entity_type: str, entity_key: str, locale: str,
    ) -> list[tuple[str, str]] | None:
        """Admin-curated FAQs → (question, answer) pairs for FAQPage
        JSON-LD. Prefers the requested locale; falls back to the default
        locale so an English-only FAQ still renders on the bn page rather
        than dropping the block. Returns None when there are no rows."""
        norm = normalise_locale(locale)
        repo = EntityFaqRepository(self.session)
        rows = await repo.list_active_for(
            entity_type=entity_type, entity_key=entity_key, locale=norm,
        )
        if not rows and norm != DEFAULT_LOCALE:
            rows = await repo.list_active_for(
                entity_type=entity_type, entity_key=entity_key,
                locale=DEFAULT_LOCALE,
            )
        return [(r.question, r.answer) for r in rows] or None

    async def for_product(
        self, product_id: UUID, *, locale: str = DEFAULT_LOCALE,
    ) -> SeoBundle:
        ctx = site_context_from_settings()
        product = await self.session.get(Product, product_id)
        if product is None:
            raise EntityNotFoundError(f"Product {product_id} not found.")
        brand = (
            await self.session.get(Brand, product.brand_id)
            if product.brand_id else None
        )
        category = (
            await self.session.get(Category, product.category_id)
            if product.category_id else None
        )

        # Resolve real catalog data for the JSON-LD Product schema.
        # Without these, search engines won't render rich snippets.
        from sqlalchemy import asc, func as sa_func, select as sa_select
        from app.modules.catalog.models import ProductMedia, ProductVariant
        from app.modules.inventory.models import StockBalance

        # Cheapest active variant — drives the displayed price/SKU.
        cheapest = (
            await self.session.execute(
                sa_select(
                    ProductVariant.id,
                    ProductVariant.price,
                    ProductVariant.sku,
                )
                .where(
                    ProductVariant.product_id == product_id,
                    ProductVariant.is_active.is_(True),
                )
                .order_by(asc(ProductVariant.price))
                .limit(1),
            )
        ).first()
        cheapest_variant_id = cheapest[0] if cheapest else None
        price = cheapest[1] if cheapest else None
        sku = cheapest[2] if cheapest else None

        # Primary image: variant-specific media first, fall back to
        # product-level. Empty media → bundle uses default OG image.
        primary_image_url = None
        if cheapest_variant_id is not None:
            row = (
                await self.session.execute(
                    sa_select(ProductMedia.url)
                    .where(
                        ProductMedia.variant_id == cheapest_variant_id,
                        ProductMedia.kind == "image",
                    )
                    .order_by(ProductMedia.position)
                    .limit(1),
                )
            ).first()
            primary_image_url = row[0] if row else None
        if primary_image_url is None:
            row = (
                await self.session.execute(
                    sa_select(ProductMedia.url)
                    .where(
                        ProductMedia.product_id == product_id,
                        ProductMedia.kind == "image",
                    )
                    .order_by(ProductMedia.position)
                    .limit(1),
                )
            ).first()
            primary_image_url = row[0] if row else None

        # In-stock = ANY active variant has > 0 in the available bucket
        # across any warehouse. We don't need exact totals here — just
        # the InStock vs OutOfStock JSON-LD availability.
        stock_total = (
            await self.session.execute(
                sa_select(
                    sa_func.coalesce(sa_func.sum(StockBalance.quantity), 0),
                )
                .join(
                    ProductVariant,
                    ProductVariant.id == StockBalance.variant_id,
                )
                .where(
                    ProductVariant.product_id == product_id,
                    ProductVariant.is_active.is_(True),
                    StockBalance.bucket == "available",
                ),
            )
        ).scalar_one()
        in_stock = bool(stock_total and int(stock_total) > 0)

        # Denormalised review stats drive the AggregateRating JSON-LD
        # block — only approved reviews are counted (the aggregate is
        # recomputed on every moderation transition). Missing row or
        # zero reviews → rating stays None so the block is omitted
        # (Google penalises empty/fake aggregateRating).
        from app.modules.reviews.models import ProductReviewAggregate

        agg = await self.session.get(ProductReviewAggregate, product_id)
        rating = None
        review_count = None
        if agg is not None and agg.review_count and int(agg.review_count) > 0:
            rating = agg.avg_rating
            review_count = int(agg.review_count)

        adapted = _ProductAdapter(
            product=product, brand=brand,
            price=price, sku=sku,
            primary_image_url=primary_image_url,
            in_stock=in_stock,
            rating=rating, review_count=review_count,
        )
        crumbs: list[tuple[str, str]] = [("Home", "/")]
        if category is not None:
            crumbs.append(
                (category.name, f"/category/{category.slug}"),
            )
        crumbs.append((product.name, f"/product/{product.slug}"))

        faqs = await self._faqs_for(
            entity_type=SeoEntityType.PRODUCT.value,
            entity_key=product.id.hex,
            locale=locale,
        )

        bundle = builders.build_product_meta(
            ctx=ctx, product=adapted, breadcrumb_trail=crumbs, faqs=faqs,
        )
        override = await self.overrides.get(
            entity_type=SeoEntityType.PRODUCT.value,
            entity_key=product.id.hex,
        )
        bundle = apply_override(bundle, override=override)
        return await self._finalise(
            bundle, ctx=ctx,
            entity_type=SeoEntityType.PRODUCT.value,
            entity_key=product.id.hex,
            locale=normalise_locale(locale),
        )

    async def for_category(
        self, category_id: UUID, *, locale: str = DEFAULT_LOCALE,
    ) -> SeoBundle:
        ctx = site_context_from_settings()
        category = await self.session.get(Category, category_id)
        if category is None:
            raise EntityNotFoundError(f"Category {category_id} not found.")
        faqs = await self._faqs_for(
            entity_type=SeoEntityType.CATEGORY.value,
            entity_key=category.id.hex,
            locale=locale,
        )
        bundle = builders.build_category_meta(
            ctx=ctx, category=category, faqs=faqs,
        )
        override = await self.overrides.get(
            entity_type=SeoEntityType.CATEGORY.value,
            entity_key=category.id.hex,
        )
        bundle = apply_override(bundle, override=override)
        return await self._finalise(
            bundle, ctx=ctx,
            entity_type=SeoEntityType.CATEGORY.value,
            entity_key=category.id.hex,
            locale=normalise_locale(locale),
        )

    async def for_brand(
        self, brand_id: UUID, *, locale: str = DEFAULT_LOCALE,
    ) -> SeoBundle:
        ctx = site_context_from_settings()
        brand = await self.session.get(Brand, brand_id)
        if brand is None:
            raise EntityNotFoundError(f"Brand {brand_id} not found.")
        bundle = builders.build_brand_meta(ctx=ctx, brand=brand)
        override = await self.overrides.get(
            entity_type=SeoEntityType.BRAND.value,
            entity_key=brand.id.hex,
        )
        bundle = apply_override(bundle, override=override)
        return await self._finalise(
            bundle, ctx=ctx,
            entity_type=SeoEntityType.BRAND.value,
            entity_key=brand.id.hex,
            locale=normalise_locale(locale),
        )

    async def for_blog_post_by_slug(
        self, slug: str, *, locale: str = DEFAULT_LOCALE,
    ) -> SeoBundle:
        ctx = site_context_from_settings()
        repo = BlogPostRepository(self.session)
        post = await repo.get_by_slug(slug)
        if post is None or post.status != BlogPostStatus.PUBLISHED.value:
            raise BlogPostNotFoundError("Blog post not found.")
        bundle = builders.build_blog_post_meta(ctx=ctx, post=post)
        override = await self.overrides.get(
            entity_type=SeoEntityType.BLOG_POST.value,
            entity_key=post.id.hex,
        )
        bundle = apply_override(bundle, override=override)
        return await self._finalise(
            bundle, ctx=ctx,
            entity_type=SeoEntityType.BLOG_POST.value,
            entity_key=post.id.hex,
            locale=normalise_locale(locale),
        )


class _ProductAdapter:
    """Duck-typed shim — exposes the fields builders.build_product_meta
    expects without coupling builders to the catalog schema.

    Caller fills price / sku / primary_image_url / in_stock from the
    catalog joins (see ``SeoBundleService.for_product``); rating +
    review_count come from ``product_review_aggregates`` (None when the
    product has no approved reviews).
    """

    __slots__ = ("_p", "_brand", "id", "name", "slug", "description",
                 "primary_image_url", "currency", "in_stock",
                 "price", "sku", "rating", "review_count", "brand")

    def __init__(
        self, *,
        product,
        brand,
        price=None,
        sku=None,
        primary_image_url=None,
        in_stock=False,
        rating=None,
        review_count=None,
    ):
        self._p = product
        self._brand = brand
        self.id = product.id
        self.name = product.name
        self.slug = product.slug
        self.description = product.description or product.short_description
        self.primary_image_url = primary_image_url
        self.currency = product.base_currency
        self.in_stock = in_stock
        self.price = price
        self.sku = sku
        # From product_review_aggregates (approved reviews only). None
        # when the product has no reviews → builders omits the
        # AggregateRating block (Google penalizes empty/fake counts).
        self.rating = rating
        self.review_count = review_count
        self.brand = brand


# ============================================================
#  SeoAdminService
# ============================================================
class SeoAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.overrides = SeoMetaOverrideRepository(session)
        # Module 34 multi-language sprint
        self.translations = SeoMetaTranslationRepository(session)
        self.banners = HomepageBannerRepository(session)
        self.posts = BlogPostRepository(session)
        self.redirects = UrlRedirectRepository(session)
        self.faqs = EntityFaqRepository(session)

    # ---------------- Translations (Module 34 multi-language) ----------------
    async def upsert_translation(
        self, *,
        entity_type: str,
        entity_key: str,
        locale: str,
        principal: Principal,
        **fields: Any,
    ):
        if entity_type not in [e.value for e in SeoEntityType]:
            raise ValidationError(f"Unknown entity_type: {entity_type}")
        norm = normalise_locale(locale)
        if norm != locale.lower().split("-", 1)[0]:
            # Caller asked for a locale that maps to default via fallback —
            # refuse rather than silently rewriting the locale column.
            raise ValidationError(
                f"Unsupported locale '{locale}'. "
                f"Supported: {', '.join(SUPPORTED_LOCALES)}",
            )
        clean = {k: v for k, v in fields.items() if v is not None}
        row = await self.translations.upsert(
            entity_type=entity_type,
            entity_key=entity_key,
            locale=norm,
            updated_by=principal.user_id,
            **clean,
        )
        await record_audit(
            actor=principal,
            action="seo.translation.upserted",
            resource_type="seo_meta_translation",
            resource_id=row.id,
            metadata={
                "entity_type": entity_type,
                "entity_key": entity_key,
                "locale": norm,
                "fields": list(clean.keys()),
            },
        )
        return row

    async def list_translations(
        self, *, entity_type: str, entity_key: str,
    ):
        return await self.translations.list_all_for_entity(
            entity_type=entity_type, entity_key=entity_key,
        )

    async def delete_translation(
        self, *, translation_id: UUID, principal: Principal,
    ):
        n = await self.translations.delete(translation_id)
        if n == 0:
            raise SeoOverrideNotFoundError("Translation not found.")
        await record_audit(
            actor=principal,
            action="seo.translation.deleted",
            resource_type="seo_meta_translation",
            resource_id=translation_id,
        )

    # ---------------- Entity FAQs (product / category / brand) ----------------
    async def list_faqs(self, *, entity_type: str, entity_key: str):
        return await self.faqs.list_all_for(
            entity_type=entity_type, entity_key=entity_key,
        )

    async def create_faq(
        self, *, entity_type: str, entity_key: str,
        principal: Principal, **fields: Any,
    ):
        if entity_type not in [e.value for e in SeoEntityType]:
            raise ValidationError(f"Unknown entity_type: {entity_type}")
        row = await self.faqs.add(
            entity_type=entity_type, entity_key=entity_key, **fields,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_FAQ_CREATED,
            resource_type="entity_faq",
            resource_id=row.id,
            metadata={
                "entity_type": entity_type,
                "entity_key": entity_key,
                "locale": row.locale,
            },
        )
        return row

    async def update_faq(
        self, *, faq_id: UUID, principal: Principal, **fields: Any,
    ):
        clean = {k: v for k, v in fields.items() if v is not None}
        n = await self.faqs.update(faq_id=faq_id, **clean)
        if n == 0:
            raise EntityNotFoundError("FAQ not found.")
        await record_audit(
            actor=principal,
            action=codes.ACTION_FAQ_UPDATED,
            resource_type="entity_faq",
            resource_id=faq_id,
            metadata={"fields": list(clean.keys())},
        )
        return await self.faqs.get(faq_id)

    async def delete_faq(self, *, faq_id: UUID, principal: Principal):
        n = await self.faqs.delete(faq_id)
        if n == 0:
            raise EntityNotFoundError("FAQ not found.")
        await record_audit(
            actor=principal,
            action=codes.ACTION_FAQ_DELETED,
            resource_type="entity_faq",
            resource_id=faq_id,
        )

    async def bulk_create_faqs(
        self,
        *,
        items: list[dict[str, Any]],
        principal: Principal,
        skip_duplicates: bool = True,
    ) -> dict[str, Any]:
        """Bulk insert FAQ rows (task #169, 2026-05-25).

        Returns a per-batch summary: requested/created/skipped/failed
        + up to 20 error strings. When ``skip_duplicates`` is True a row
        already present (same entity_type+entity_key+locale+question)
        is silently skipped; when False such collisions are recorded as
        failures and counted against the batch.

        Each create still routes through ``create_faq`` so the audit
        trail (``codes.ACTION_FAQ_CREATED``) fires per row — auditors
        can replay the batch from the audit log alone.
        """
        requested = len(items)
        created = 0
        skipped = 0
        failed = 0
        errors: list[str] = []

        # Index existing rows ONCE per (entity_type, entity_key) pair we
        # see in the batch so the dupe-check is O(1) inside the loop.
        seen_existing: dict[tuple[str, str], set[tuple[str, str]]] = {}

        async def _load_existing(et: str, ek: str) -> set[tuple[str, str]]:
            key = (et, ek)
            if key in seen_existing:
                return seen_existing[key]
            rows = await self.faqs.list_all_for(
                entity_type=et, entity_key=ek,
            )
            seen_existing[key] = {(r.locale, r.question.strip()) for r in rows}
            return seen_existing[key]

        for idx, raw in enumerate(items):
            entity_type = raw.get("entity_type", "")
            entity_key = raw.get("entity_key", "")
            locale = raw.get("locale", "en")
            question = (raw.get("question") or "").strip()
            try:
                if entity_type not in [e.value for e in SeoEntityType]:
                    raise ValidationError(
                        f"Unknown entity_type: {entity_type}",
                    )
                existing = await _load_existing(entity_type, entity_key)
                dupe_key = (locale, question)
                if dupe_key in existing:
                    if skip_duplicates:
                        skipped += 1
                        continue
                    raise ValidationError(
                        f"Duplicate FAQ at row {idx + 1}: "
                        f"{entity_type}/{entity_key} [{locale}] {question[:40]!r}",
                    )
                row = await self.create_faq(
                    principal=principal,
                    entity_type=entity_type,
                    entity_key=entity_key,
                    locale=locale,
                    question=question,
                    answer=raw.get("answer", ""),
                    position=int(raw.get("position", 0)),
                    is_active=bool(raw.get("is_active", True)),
                )
                # Track the newly created one so a within-batch
                # duplicate triggers the same dedupe path.
                existing.add(dupe_key)
                created += 1
            except Exception as exc:  # noqa: BLE001 — per-row soft-fail
                failed += 1
                if len(errors) < 20:
                    errors.append(f"row {idx + 1}: {exc}")
                # When skip_duplicates=False the caller WANTS the
                # whole batch rolled back on first collision; let the
                # exception bubble after the summary is built.
                if not skip_duplicates:
                    raise

        return {
            "requested": requested,
            "created": created,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
        }

    # ---------------- Overrides ----------------
    async def upsert_override(
        self, *,
        entity_type: str,
        entity_key: str,
        principal: Principal,
        **fields: Any,
    ):
        if entity_type not in [e.value for e in SeoEntityType]:
            raise ValidationError(f"Unknown entity_type: {entity_type}")
        # Drop NULL-valued fields so a partial PATCH leaves existing
        # non-NULL fields untouched on first insert.
        clean = {k: v for k, v in fields.items() if v is not None}
        row = await self.overrides.upsert(
            entity_type=entity_type,
            entity_key=entity_key,
            updated_by=principal.user_id,
            **clean,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_SEO_OVERRIDE_UPSERTED,
            resource_type="seo_meta_override",
            resource_id=row.id,
            metadata={
                "entity_type": entity_type,
                "entity_key": entity_key,
            },
        )
        return row

    async def delete_override(self, *, override_id: UUID, principal: Principal):
        n = await self.overrides.delete(override_id)
        if n == 0:
            raise SeoOverrideNotFoundError("Override not found.")
        await record_audit(
            actor=principal,
            action=codes.ACTION_SEO_OVERRIDE_DELETED,
            resource_type="seo_meta_override",
            resource_id=override_id,
        )

    # ---------------- Banners ----------------
    async def create_banner(
        self, *,
        principal: Principal,
        **fields: Any,
    ):
        row = await self.banners.add(**fields)
        await record_audit(
            actor=principal,
            action=codes.ACTION_BANNER_CREATED,
            resource_type="homepage_banner",
            resource_id=row.id,
            metadata={"title": row.title},
        )
        return row

    async def update_banner(
        self, *,
        banner_id: UUID,
        principal: Principal,
        **fields: Any,
    ):
        clean = {k: v for k, v in fields.items() if v is not None}
        if not clean:
            existing = await self.banners.get(banner_id)
            if existing is None:
                raise BannerNotFoundError("Banner not found.")
            return existing
        n = await self.banners.update(banner_id=banner_id, **clean)
        if n == 0:
            raise BannerNotFoundError("Banner not found.")
        await record_audit(
            actor=principal,
            action=codes.ACTION_BANNER_UPDATED,
            resource_type="homepage_banner",
            resource_id=banner_id,
            metadata={"fields": list(clean.keys())},
        )
        return await self.banners.get(banner_id)

    async def deactivate_banner(self, *, banner_id: UUID, principal: Principal):
        n = await self.banners.update(banner_id=banner_id, is_active=False)
        if n == 0:
            raise BannerNotFoundError("Banner not found.")
        await record_audit(
            actor=principal,
            action=codes.ACTION_BANNER_DEACTIVATED,
            resource_type="homepage_banner",
            resource_id=banner_id,
        )
        return await self.banners.get(banner_id)

    # ---------------- Blog posts ----------------
    async def create_blog_post(
        self, *,
        principal: Principal,
        slug: str,
        **fields: Any,
    ):
        existing = await self.posts.get_by_slug(slug)
        if existing is not None:
            raise BlogSlugTakenError(
                f"Blog slug '{slug}' is already in use.",
            )
        row = await self.posts.add(
            slug=slug,
            author_user_id=principal.user_id,
            **fields,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_BLOG_POST_CREATED,
            resource_type="blog_post",
            resource_id=row.id,
            metadata={"slug": slug, "status": row.status},
        )
        return row

    async def update_blog_post(
        self, *,
        post_id: UUID,
        principal: Principal,
        **fields: Any,
    ):
        clean = {k: v for k, v in fields.items() if v is not None}
        n = await self.posts.update(post_id=post_id, **clean) if clean else 0
        post = await self.posts.get(post_id)
        if post is None:
            raise BlogPostNotFoundError("Blog post not found.")
        if n > 0:
            await record_audit(
                actor=principal,
                action=codes.ACTION_BLOG_POST_UPDATED,
                resource_type="blog_post",
                resource_id=post_id,
                metadata={"fields": list(clean.keys())},
            )
        return post

    async def publish_blog_post(self, *, post_id: UUID, principal: Principal):
        n = await self.posts.update(
            post_id=post_id,
            status=BlogPostStatus.PUBLISHED.value,
            published_at=utc_now(),
        )
        if n == 0:
            raise BlogPostNotFoundError("Blog post not found.")
        await record_audit(
            actor=principal,
            action=codes.ACTION_BLOG_POST_PUBLISHED,
            resource_type="blog_post",
            resource_id=post_id,
        )
        post = await self.posts.get(post_id)
        # IndexNow — ping Bing/Yandex with the freshly published URL so
        # the post is crawlable within minutes instead of next-sitemap.
        # Soft-import to avoid coupling the service to the jobs module
        # at import time (jobs module imports httpx which the unit-test
        # rig stubs out).
        try:
            from app.modules.seo.jobs import enqueue_blog_url
            if post and post.slug:
                enqueue_blog_url(post.slug)
        except Exception:  # noqa: BLE001
            pass
        return post

    async def archive_blog_post(self, *, post_id: UUID, principal: Principal):
        n = await self.posts.update(
            post_id=post_id, status=BlogPostStatus.ARCHIVED.value,
        )
        if n == 0:
            raise BlogPostNotFoundError("Blog post not found.")
        await record_audit(
            actor=principal,
            action=codes.ACTION_BLOG_POST_ARCHIVED,
            resource_type="blog_post",
            resource_id=post_id,
        )
        return await self.posts.get(post_id)

    # ---------------- Redirects ----------------
    async def create_redirect(
        self, *,
        from_path: str,
        to_path: str,
        redirect_type: str,
        principal: Principal,
        note: str | None = None,
    ):
        if from_path == to_path:
            raise RedirectLoopError(
                "from_path and to_path must differ.",
            )
        if not from_path.startswith("/"):
            raise ValidationError("from_path must start with '/'.")
        row = await self.redirects.add(
            from_path=from_path,
            to_path=to_path,
            redirect_type=redirect_type,
            note=note,
            created_by=principal.user_id,
            is_active=True,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_REDIRECT_CREATED,
            resource_type="seo_url_redirect",
            resource_id=row.id,
            metadata={
                "from_path": from_path,
                "to_path": to_path,
                "type": redirect_type,
            },
        )
        return row

    async def delete_redirect(self, *, redirect_id: UUID, principal: Principal):
        n = await self.redirects.delete(redirect_id)
        if n == 0:
            raise RedirectNotFoundError("Redirect not found.")
        await record_audit(
            actor=principal,
            action=codes.ACTION_REDIRECT_DELETED,
            resource_type="seo_url_redirect",
            resource_id=redirect_id,
        )


# ============================================================
#  SitemapService
# ============================================================
#: Max URLs per child sitemap. Google + sitemaps.org spec hard cap is
#: 50,000 URLs OR 50MB uncompressed — whichever comes first. We cap at
#: 45,000 to leave roughly 10% size headroom for the image/video/news
#: extensions that pad each <url> entry well past the bare-URL average.
#: The pagination logic (build_sitemap_index) shards on this cap, so
#: lowering it pre-emptively shards before the catalog scales into the
#: 50MB ceiling rather than after, which is harder to detect (Google
#: silently truncates oversized sitemaps).
SITEMAP_URL_CAP = 45_000

#: Operational target the admin shard-status endpoint uses to decide
#: when to warn. When any kind exceeds 80% of the cap on its last shard,
#: ops should plan a re-shard or category split before the next deploy.
SITEMAP_SHARD_WARN_THRESHOLD = 0.80

#: Ordered child-sitemap kinds. Each maps to a catalog/content source
#: in ``SitemapService``. ``static`` is the fixed marketing-page list.
SITEMAP_KINDS = ("static", "products", "categories", "brands", "blog")

#: Per-kind crawl-priority hints (0.0-1.0). Crawlers treat these as
#: RELATIVE within the site, not absolute — bumping everything to 1.0
#: is the same as leaving them all at 0.5. Home/static get the top
#: slot because they're the entry points; products + categories take
#: the bulk of organic traffic; blog trails because each post has its
#: own internal-link cluster on the BlogPosting page anyway.
SITEMAP_PRIORITY: dict[str, str] = {
    "static": "1.0",
    "products": "0.9",
    "categories": "0.8",
    "brands": "0.7",
    "blog": "0.6",
}

#: Per-kind ``changefreq`` hint. Crawlers ignore this in practice but
#: emitting it keeps the sitemap valid against legacy validators +
#: signals freshness intent to Bing.
SITEMAP_CHANGEFREQ: dict[str, str] = {
    "static": "monthly",
    "products": "daily",
    "categories": "weekly",
    "brands": "monthly",
    "blog": "weekly",
}


class SitemapService:
    """Builds a sitemap **index** plus per-type child sitemaps.

      ``/sitemap.xml``              → ``<sitemapindex>`` listing children
      ``/sitemap-{kind}-{n}.xml``   → ``<urlset>`` slice (``n`` 0-based),
                                      kind in :data:`SITEMAP_KINDS`,
                                      capped at :data:`SITEMAP_URL_CAP`.

    Splitting keeps every child well under the 50k / 50MB spec limits
    once the catalog grows past a single file, and lets crawlers fetch
    only the section that changed (``lastmod`` on each index entry is
    the newest row in that section).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- static marketing pages (no lastmod) ----------------------
    @staticmethod
    def _static_urls(base: str) -> list[tuple[str, datetime | date | None]]:
        paths = (
            "/", "/products", "/categories", "/brands",
            "/blog", "/deals", "/prescription/upload",
        )
        return [(f"{base}{p}", None) for p in paths]

    # -- per-kind count + newest lastmod (drives the index) -------
    async def _kind_count_and_lastmod(
        self, kind: str,
    ) -> tuple[int, datetime | date | None]:
        if kind == "static":
            return len(self._static_urls("")), None
        col, where, stamp = self._kind_query_parts(kind)
        row = (
            await self.session.execute(
                select(func.count(), func.max(stamp)).where(where),
            )
        ).first()
        return (int(row[0]) if row else 0), (row[1] if row else None)

    # -- per-kind URL slice for child sitemap page n -------------
    async def _kind_url_slice(
        self, kind: str, base: str, page: int,
    ) -> list[tuple[str, datetime | date | None]]:
        if kind == "static":
            return self._static_urls(base) if page == 0 else []
        slug_col, where, stamp = self._kind_query_parts(kind)
        prefix = {
            "products": "/product/",
            "categories": "/category/",
            "brands": "/brand/",
            "blog": "/blog/",
        }[kind]
        rows = (
            await self.session.execute(
                select(slug_col, stamp)
                .where(where)
                .order_by(slug_col)
                .offset(page * SITEMAP_URL_CAP)
                .limit(SITEMAP_URL_CAP),
            )
        ).all()
        return [(f"{base}{prefix}{slug}", ts) for slug, ts in rows]

    # -- per-product primary image (sitemap-image extension) -----
    async def _product_image_map(
        self, slugs: list[str],
    ) -> dict[str, str]:
        """Return {product_slug: primary_image_url} for the given slugs.

        Used to populate ``<image:image>`` nodes in the product sitemap.
        Misses (no image / not found) just omit the image node — the
        URL still ships, crawlers tolerate sparse coverage.
        """
        if not slugs:
            return {}
        from app.modules.catalog.models import ProductMedia
        rows = (
            await self.session.execute(
                select(Product.slug, ProductMedia.url)
                .join(ProductMedia, ProductMedia.product_id == Product.id)
                .where(
                    Product.slug.in_(slugs),
                    ProductMedia.kind == "image",
                )
                .order_by(Product.slug, ProductMedia.position),
            )
        ).all()
        # First image per slug wins (ordered by position).
        out: dict[str, str] = {}
        for slug, url in rows:
            if slug not in out:
                out[slug] = url
        return out

    # -- per-product video metadata (sitemap-video extension) ----
    async def _product_video_map(
        self, slugs: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Return {product_slug: [{title, hls_url, thumbnail_url,
        duration_seconds}, ...]} for the given slugs. Only ``approved``
        videos surface (matches the public list endpoint). Misses are
        omitted — the URL still ships, crawlers tolerate sparse coverage.

        Capped at 5 videos per product to match the public list endpoint
        + keep sitemap XML size bounded under the 50MB per-file cap.
        """
        if not slugs:
            return {}
        from app.modules.product_videos.models import ProductVideo
        rows = (
            await self.session.execute(
                select(
                    Product.slug,
                    ProductVideo.title,
                    ProductVideo.hls_url,
                    ProductVideo.thumbnail_url,
                    ProductVideo.duration_seconds,
                )
                .join(ProductVideo, ProductVideo.product_id == Product.id)
                .where(
                    Product.slug.in_(slugs),
                    ProductVideo.status == "approved",
                    ProductVideo.hls_url.is_not(None),
                )
                .order_by(Product.slug, ProductVideo.created_at.desc()),
            )
        ).all()
        out: dict[str, list[dict[str, Any]]] = {}
        for slug, title, hls, thumb, dur in rows:
            bucket = out.setdefault(slug, [])
            if len(bucket) < 5:
                bucket.append({
                    "title": title or "",
                    "hls_url": hls,
                    "thumbnail_url": thumb,
                    "duration_seconds": dur,
                })
        return out

    # -- per-blog news metadata (sitemap-news extension) ---------
    async def _blog_news_map(
        self, slugs: list[str],
    ) -> dict[str, tuple[str, datetime | date | None]]:
        """Return {blog_slug: (title, published_at)} for posts published
        in the last 48 hours — Google News only honours ``<news:news>``
        nodes for fresh content, so we filter the rest out at query time
        to keep the XML small."""
        if not slugs:
            return {}
        from app.core.time import utc_now
        from datetime import timedelta
        cutoff = utc_now() - timedelta(hours=48)
        rows = (
            await self.session.execute(
                select(BlogPost.slug, BlogPost.title, BlogPost.published_at)
                .where(
                    BlogPost.slug.in_(slugs),
                    BlogPost.status == BlogPostStatus.PUBLISHED.value,
                    BlogPost.published_at.is_not(None),
                    BlogPost.published_at >= cutoff,
                ),
            )
        ).all()
        return {slug: (title, ts) for slug, title, ts in rows}

    @staticmethod
    def _kind_query_parts(kind: str):
        """Return (slug_or_count_col, where_clause, lastmod_col) per kind."""
        if kind == "products":
            return Product.slug, Product.status == "active", Product.updated_at
        if kind == "categories":
            return Category.slug, Category.is_active.is_(True), Category.updated_at
        if kind == "brands":
            return Brand.slug, Brand.is_active.is_(True), Brand.updated_at
        if kind == "blog":
            return (
                BlogPost.slug,
                BlogPost.status == BlogPostStatus.PUBLISHED.value,
                BlogPost.published_at,
            )
        raise ValueError(f"Unknown sitemap kind: {kind!r}")

    async def build_sitemap_index(self) -> str:
        from math import ceil

        ctx = site_context_from_settings()
        base = ctx.site_url.rstrip("/")
        entries: list[tuple[str, datetime | date | None]] = []
        for kind in SITEMAP_KINDS:
            count, lastmod = await self._kind_count_and_lastmod(kind)
            if count == 0:
                continue
            pages = max(1, ceil(count / SITEMAP_URL_CAP))
            for n in range(pages):
                entries.append((f"{base}/sitemap-{kind}-{n}.xml", lastmod))
        return _render_sitemap_index(entries)

    async def build_child_sitemap(self, kind: str, page: int) -> str:
        if kind not in SITEMAP_KINDS:
            raise ValueError(f"Unknown sitemap kind: {kind!r}")
        ctx = site_context_from_settings()
        base = ctx.site_url.rstrip("/")
        urls = await self._kind_url_slice(kind, base, page)

        # Sitemap-image + sitemap-video extensions — only ship on the
        # products sitemap (categories/brands/blog ship image-less). We
        # batch-lookup primary image + approved videos per slug so the
        # XML grows by image+video nodes with two extra queries.
        image_map: dict[str, str] = {}
        video_map: dict[str, list[dict[str, Any]]] = {}
        if kind == "products" and urls:
            slugs = [u[0].rsplit("/product/", 1)[-1] for u in urls]
            image_map = await self._product_image_map(slugs)
            video_map = await self._product_video_map(slugs)

        # Sitemap-news extension — only blog URLs with published_at in
        # the last 48h carry <news:news>. Older posts ship as plain
        # entries (the news namespace ignores stale items).
        news_map: dict[str, tuple[str, datetime | date | None]] = {}
        if kind == "blog" and urls:
            slugs = [u[0].rsplit("/blog/", 1)[-1] for u in urls]
            news_map = await self._blog_news_map(slugs)

        return _render_sitemap(
            urls,
            supported_locales=list(ctx.supported_locales),
            default_locale=ctx.default_locale,
            image_map=image_map,
            news_map=news_map,
            video_map=video_map,
            site_name=ctx.site_name,
            priority=SITEMAP_PRIORITY.get(kind),
            changefreq=SITEMAP_CHANGEFREQ.get(kind),
        )

    async def shard_status(self) -> dict[str, object]:
        """Per-kind sitemap shard health (task #170, 2026-05-25).

        Returns the URL count, current shard count, the fill percentage
        of the last shard, and a ``next_shard_at`` threshold so ops can
        see how much room is left before the next file is added. When
        the last shard breaches :data:`SITEMAP_SHARD_WARN_THRESHOLD`,
        the ``warn`` flag flips True for that kind.
        """
        from math import ceil
        kinds: list[dict[str, object]] = []
        worst_fill = 0.0
        for kind in SITEMAP_KINDS:
            count, _lastmod = await self._kind_count_and_lastmod(kind)
            shards = max(1, ceil(count / SITEMAP_URL_CAP)) if count else 0
            last_shard_urls = (
                count - (shards - 1) * SITEMAP_URL_CAP if shards else 0
            )
            fill_pct = (
                round(100.0 * last_shard_urls / SITEMAP_URL_CAP, 2)
                if shards else 0.0
            )
            worst_fill = max(worst_fill, fill_pct)
            kinds.append({
                "kind": kind,
                "url_count": int(count),
                "shards": int(shards),
                "last_shard_urls": int(last_shard_urls),
                "last_shard_fill_pct": fill_pct,
                "next_shard_at": int(shards * SITEMAP_URL_CAP) if shards else SITEMAP_URL_CAP,
                "warn": fill_pct >= SITEMAP_SHARD_WARN_THRESHOLD * 100,
            })
        return {
            "cap_per_shard": SITEMAP_URL_CAP,
            "warn_threshold_pct": SITEMAP_SHARD_WARN_THRESHOLD * 100,
            "kinds": kinds,
            "worst_fill_pct": worst_fill,
            "any_warn": any(k["warn"] for k in kinds),
        }

    async def build_robots_txt(self) -> str:
        ctx = site_context_from_settings()
        sitemap_url = f"{ctx.site_url.rstrip('/')}/sitemap.xml"
        return (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "Disallow: /admin/\n"
            f"Sitemap: {sitemap_url}\n"
        )


def _render_sitemap_index(
    entries: list[tuple[str, datetime | date | None]],
) -> str:
    """Build a ``<sitemapindex>`` XML string listing child sitemaps.

    Each entry is ``(loc, lastmod)``; ``lastmod`` is written when present.
    """
    parts: list[str] = ["<?xml version='1.0' encoding='UTF-8'?>"]
    parts.append(
        "<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>",
    )
    for loc, lastmod in entries:
        lines = ["  <sitemap>", f"    <loc>{_xml_escape(loc)}</loc>"]
        if lastmod is not None:
            stamp = (
                lastmod.date().isoformat()
                if isinstance(lastmod, datetime) else lastmod.isoformat()
            )
            lines.append(f"    <lastmod>{stamp}</lastmod>")
        lines.append("  </sitemap>")
        parts.append("\n".join(lines))
    parts.append("</sitemapindex>")
    return "\n".join(parts)


def _render_sitemap(
    urls: list[tuple[str, datetime | date | None]],
    *,
    supported_locales: list[str] | None = None,
    default_locale: str = "en",
    image_map: dict[str, str] | None = None,
    news_map: dict[str, tuple[str, datetime | date | None]] | None = None,
    video_map: dict[str, list[dict[str, Any]]] | None = None,
    site_name: str = "Hypershop",
    priority: str | None = None,
    changefreq: str | None = None,
) -> str:
    """Build a sitemap XML string. ``lastmod`` written when present.

    When ``supported_locales`` has more than one entry, each ``<url>``
    block also gets ``<xhtml:link rel="alternate" hreflang="X">`` tags
    per locale plus an ``x-default`` row pointing to the default-locale
    URL. The ``xhtml`` namespace is declared on the root ``<urlset>``.

    ``image_map`` is a {url-suffix-slug: image_url} dict used to add an
    ``<image:image>`` node per URL that has a known primary image. The
    slug is the part after the last ``/`` (e.g. ``a4-paper-double-a-500``
    for ``/product/a4-paper-double-a-500``). When empty, the image
    namespace is not declared.

    ``news_map`` is a {url-suffix-slug: (title, published_at)} dict used
    to add a ``<news:news>`` node for blog posts <48h old. When empty,
    the news namespace is not declared.
    """
    locales = supported_locales or [default_locale]
    has_alternates = len(locales) > 1
    has_images = bool(image_map)
    has_news = bool(news_map)
    has_videos = bool(video_map)

    def alt_url(base: str, locale: str) -> str:
        if locale == default_locale:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}lang={locale}"

    def slug_of(url: str) -> str:
        return url.rsplit("/", 1)[-1]

    parts: list[str] = []
    parts.append("<?xml version='1.0' encoding='UTF-8'?>")
    root_attrs = ["xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'"]
    if has_alternates:
        root_attrs.append("xmlns:xhtml='http://www.w3.org/1999/xhtml'")
    if has_images:
        root_attrs.append(
            "xmlns:image='http://www.google.com/schemas/sitemap-image/1.1'",
        )
    if has_news:
        root_attrs.append(
            "xmlns:news='http://www.google.com/schemas/sitemap-news/0.9'",
        )
    if has_videos:
        root_attrs.append(
            "xmlns:video='http://www.google.com/schemas/sitemap-video/1.1'",
        )
    parts.append(f"<urlset {' '.join(root_attrs)}>")
    for loc, lastmod in urls:
        loc_xml = _xml_escape(loc)
        url_lines: list[str] = ["  <url>", f"    <loc>{loc_xml}</loc>"]
        if lastmod is not None:
            stamp = (
                lastmod.date().isoformat()
                if isinstance(lastmod, datetime) else lastmod.isoformat()
            )
            url_lines.append(f"    <lastmod>{stamp}</lastmod>")
        if changefreq:
            url_lines.append(f"    <changefreq>{changefreq}</changefreq>")
        if priority:
            url_lines.append(f"    <priority>{priority}</priority>")
        if has_alternates:
            for lc in locales:
                href = _xml_escape(alt_url(loc, lc))
                url_lines.append(
                    f"    <xhtml:link rel='alternate' "
                    f"hreflang='{lc}' href='{href}'/>",
                )
            xd_href = _xml_escape(alt_url(loc, default_locale))
            url_lines.append(
                f"    <xhtml:link rel='alternate' "
                f"hreflang='x-default' href='{xd_href}'/>",
            )
        if has_images:
            img = (image_map or {}).get(slug_of(loc))
            if img:
                url_lines.append("    <image:image>")
                url_lines.append(
                    f"      <image:loc>{_xml_escape(img)}</image:loc>",
                )
                url_lines.append("    </image:image>")
        if has_videos:
            vids = (video_map or {}).get(slug_of(loc), [])
            for v in vids:
                url_lines.append("    <video:video>")
                if v.get("thumbnail_url"):
                    url_lines.append(
                        f"      <video:thumbnail_loc>"
                        f"{_xml_escape(v['thumbnail_url'])}"
                        f"</video:thumbnail_loc>",
                    )
                url_lines.append(
                    f"      <video:title>"
                    f"{_xml_escape(v.get('title') or 'Product video')}"
                    f"</video:title>",
                )
                url_lines.append(
                    f"      <video:description>"
                    f"{_xml_escape(v.get('title') or 'Product video')}"
                    f"</video:description>",
                )
                url_lines.append(
                    f"      <video:content_loc>"
                    f"{_xml_escape(v['hls_url'])}"
                    f"</video:content_loc>",
                )
                if v.get("duration_seconds"):
                    url_lines.append(
                        f"      <video:duration>"
                        f"{int(v['duration_seconds'])}"
                        f"</video:duration>",
                    )
                url_lines.append(
                    "      <video:family_friendly>yes</video:family_friendly>",
                )
                url_lines.append("    </video:video>")
        if has_news:
            entry = (news_map or {}).get(slug_of(loc))
            if entry:
                title, pub = entry
                pub_iso = (
                    pub.isoformat()
                    if isinstance(pub, (datetime, date)) else str(pub)
                )
                url_lines.append("    <news:news>")
                url_lines.append("      <news:publication>")
                url_lines.append(
                    f"        <news:name>{_xml_escape(site_name)}</news:name>",
                )
                url_lines.append(
                    "        <news:language>en</news:language>",
                )
                url_lines.append("      </news:publication>")
                url_lines.append(
                    f"      <news:publication_date>{pub_iso}"
                    f"</news:publication_date>",
                )
                url_lines.append(
                    f"      <news:title>{_xml_escape(title)}</news:title>",
                )
                url_lines.append("    </news:news>")
        url_lines.append("  </url>")
        parts.append("\n".join(url_lines))
    parts.append("</urlset>")
    return "\n".join(parts)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&apos;")
    )
