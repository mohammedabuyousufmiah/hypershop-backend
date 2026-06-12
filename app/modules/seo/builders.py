"""Pure-function SEO bundle builders.

Each ``build_*_meta`` produces a complete ``SeoBundle`` for a given
entity (product / category / brand / blog post / static page). The
service layer overlays any ``SeoMetaOverride`` row on top.

Bundles include:
  - meta tags  (title, description, canonical, OG image, twitter card)
  - JSON-LD   (organization + breadcrumb + entity-specific schema)

No DB I/O — caller is responsible for fetching the source row(s).
This makes every builder trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.modules.seo.state import (
    AVAILABILITY_IN_STOCK,
    AVAILABILITY_OUT_OF_STOCK,
    JSONLD_TYPE_AGGREGATE_RATING,
    JSONLD_TYPE_ANSWER,
    JSONLD_TYPE_BLOG_POSTING,
    JSONLD_TYPE_BREADCRUMB_LIST,
    JSONLD_TYPE_FAQ_PAGE,
    JSONLD_TYPE_LIST_ITEM,
    JSONLD_TYPE_OFFER,
    JSONLD_TYPE_ORGANIZATION,
    JSONLD_TYPE_PRODUCT,
    JSONLD_TYPE_QUESTION,
    OgType,
    SCHEMA_CONTEXT,
    TwitterCardType,
)


# ----------------------------------------------------------------------
# Bundle dataclass
# ----------------------------------------------------------------------
@dataclass(slots=True)
class SeoBundle:
    """Wire shape returned by /api/v1/seo/meta/* endpoints.

    Frontend renders these into ``<head>``. Field naming matches what
    Google's structured-data validator expects.
    """

    title: str
    meta_title: str
    meta_description: str
    canonical_url: str
    og_type: str
    og_image: str | None
    og_url: str
    twitter_card: str
    robots: str | None = None
    extra_meta: dict[str, str] = field(default_factory=dict)
    jsonld: list[dict[str, Any]] = field(default_factory=list)
    # Module 34 multi-language sprint: which locale this bundle was
    # rendered in, and the hreflang alternates (one per available locale
    # plus x-default) for the frontend to render in <head>.
    locale: str = "en"
    hreflang: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class SiteContext:
    """Per-request site config — passed into every builder.

    Comes from ``settings.site_*`` and is shaped here so builders
    don't import the settings singleton (keeps them pure).
    """

    site_name: str
    site_url: str
    default_og_image: str | None
    default_twitter_card: str = TwitterCardType.SUMMARY_LARGE_IMAGE.value
    # NAP (Name-Address-Phone) — required for LocalBusiness rich results.
    # Field names kept as ``pharmacy_*`` for backwards-compat with any
    # downstream caller that constructs SiteContext directly; the values
    # populate the schema.org PostalAddress regardless of business type.
    pharmacy_phone: str | None = None
    pharmacy_locality: str = "Dhaka"
    pharmacy_country: str = "BD"
    org_street: str | None = None
    org_region: str | None = None
    org_postal_code: str | None = None
    org_lat: str | None = None
    org_lng: str | None = None
    org_price_range: str | None = None
    org_opening_hours: str | None = None
    # ContactPoint blocks — emitted under Organization.contactPoint[].
    contact_support_phone: str | None = None
    contact_sales_phone: str | None = None
    contact_billing_phone: str | None = None
    contact_languages: tuple[str, ...] = ("en", "bn")
    contact_hours: str | None = None
    # Module 34 multi-language sprint
    default_locale: str = "en"
    supported_locales: tuple[str, ...] = ("en", "bn")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def absolute_url(*, ctx: SiteContext, path: str) -> str:
    """Resolve a relative path against the site URL.

    Pass-through for already-absolute URLs (so templates can pass
    ``image_url`` from the catalog without checking).
    """
    if not path:
        return ctx.site_url
    if path.startswith(("http://", "https://")):
        return path
    return f"{ctx.site_url.rstrip('/')}/{path.lstrip('/')}"


def truncate_description(text: str, *, max_len: int = 160) -> str:
    """Trim to ~160 chars (Google SERP cap) at a word boundary."""
    if not text:
        return ""
    text = " ".join(text.split())  # collapse whitespace
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def organization_jsonld(ctx: SiteContext) -> dict[str, Any]:
    """Schema.org OnlineStore block — appears on every page so search
    engines see the brand entity consistently.

    OnlineStore inherits from both Organization and LocalBusiness, so
    populating streetAddress + geo + openingHours + priceRange unlocks
    LocalBusiness rich results (Google Maps pin, Knowledge Panel) on
    the same script tag without a second schema block.
    """
    block: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": JSONLD_TYPE_ORGANIZATION,
        "name": ctx.site_name,
        "url": ctx.site_url,
    }
    if ctx.default_og_image:
        block["logo"] = absolute_url(ctx=ctx, path=ctx.default_og_image)
    if ctx.pharmacy_phone:
        block["telephone"] = ctx.pharmacy_phone

    address: dict[str, Any] = {
        "@type": "PostalAddress",
        "addressCountry": ctx.pharmacy_country,
        "addressLocality": ctx.pharmacy_locality,
    }
    if ctx.org_street:
        address["streetAddress"] = ctx.org_street
    if ctx.org_region:
        address["addressRegion"] = ctx.org_region
    if ctx.org_postal_code:
        address["postalCode"] = ctx.org_postal_code
    block["address"] = address

    # GeoCoordinates — required for the Maps pin. Both lat+lng must be
    # present + parseable; otherwise Google ignores the partial block.
    if ctx.org_lat and ctx.org_lng:
        try:
            block["geo"] = {
                "@type": "GeoCoordinates",
                "latitude": float(ctx.org_lat),
                "longitude": float(ctx.org_lng),
            }
        except (ValueError, TypeError):
            # Garbage coords — drop the block rather than ship invalid
            # JSON-LD (Google penalises malformed structured data).
            pass

    if ctx.org_price_range:
        block["priceRange"] = ctx.org_price_range
    if ctx.org_opening_hours:
        # Schema.org accepts the ISO 8601 day-time token list directly
        # (e.g. "Mo-Fr 09:00-18:00,Sa 10:00-15:00") split into an array.
        block["openingHours"] = [
            tok.strip()
            for tok in ctx.org_opening_hours.split(",")
            if tok.strip()
        ]
    # ContactPoint blocks — one per populated phone. Each carries the
    # contactType + availableLanguage so Google's Knowledge Panel can
    # route callers to the right department.
    contact_points: list[dict[str, Any]] = []
    langs = list(ctx.contact_languages)
    hours = ctx.contact_hours
    for phone, ctype in (
        (ctx.contact_support_phone, "customer support"),
        (ctx.contact_sales_phone, "sales"),
        (ctx.contact_billing_phone, "billing support"),
    ):
        if not phone:
            continue
        cp: dict[str, Any] = {
            "@type": "ContactPoint",
            "telephone": phone,
            "contactType": ctype,
            "areaServed": ctx.pharmacy_country,
            "availableLanguage": langs,
        }
        if hours:
            cp["hoursAvailable"] = hours
        contact_points.append(cp)
    if contact_points:
        block["contactPoint"] = contact_points

    # sameAs — the canonical site_url provides a brand entity anchor
    # even before social profiles are wired.
    block["sameAs"] = [ctx.site_url]
    return block


def breadcrumb_jsonld(
    ctx: SiteContext,
    items: list[tuple[str, str]],
) -> dict[str, Any]:
    """Build a BreadcrumbList from [(name, url_or_path), ...]."""
    return {
        "@context": SCHEMA_CONTEXT,
        "@type": JSONLD_TYPE_BREADCRUMB_LIST,
        "itemListElement": [
            {
                "@type": JSONLD_TYPE_LIST_ITEM,
                "position": idx,
                "name": name,
                "item": absolute_url(ctx=ctx, path=path),
            }
            for idx, (name, path) in enumerate(items, start=1)
        ],
    }


def faqpage_jsonld(faqs: list[tuple[str, str]]) -> dict[str, Any]:
    """Build an FAQPage block from [(question, answer), ...].

    Each entry becomes a ``Question`` with an ``acceptedAnswer`` of type
    ``Answer``. Caller must only pass non-empty, admin-curated pairs —
    Google requires the same Q&A to be visible on the page.
    """
    return {
        "@context": SCHEMA_CONTEXT,
        "@type": JSONLD_TYPE_FAQ_PAGE,
        "mainEntity": [
            {
                "@type": JSONLD_TYPE_QUESTION,
                "name": question,
                "acceptedAnswer": {
                    "@type": JSONLD_TYPE_ANSWER,
                    "text": answer,
                },
            }
            for question, answer in faqs
        ],
    }


# ----------------------------------------------------------------------
# Per-entity builders — pure
# ----------------------------------------------------------------------
def build_home_meta(*, ctx: SiteContext) -> SeoBundle:
    title = ctx.site_name
    description = truncate_description(
        f"{ctx.site_name} — online pharmacy in Bangladesh with "
        f"genuine medicines, healthcare products, fast delivery, "
        f"and prescription support.",
    )
    canonical = ctx.site_url + "/"
    og_image = absolute_url(ctx=ctx, path=ctx.default_og_image) if ctx.default_og_image else None
    return SeoBundle(
        title=title,
        meta_title=title,
        meta_description=description,
        canonical_url=canonical,
        og_type=OgType.WEBSITE.value,
        og_image=og_image,
        og_url=canonical,
        twitter_card=ctx.default_twitter_card,
        jsonld=[
            organization_jsonld(ctx),
            breadcrumb_jsonld(ctx, [("Home", "/")]),
        ],
    )


def build_product_meta(
    *,
    ctx: SiteContext,
    product: Any,
    breadcrumb_trail: list[tuple[str, str]] | None = None,
    faqs: list[tuple[str, str]] | None = None,
) -> SeoBundle:
    """Build SEO bundle for a Product. ``product`` is duck-typed to
    expose: id, name, slug, description, price, currency, sku, brand?,
    primary_image_url?, in_stock (bool).
    """
    name = getattr(product, "name", "") or ""
    description = truncate_description(
        getattr(product, "description", "") or name,
    )
    slug = getattr(product, "slug", None) or str(getattr(product, "id", ""))
    canonical = absolute_url(ctx=ctx, path=f"/product/{slug}")
    primary_image = (
        getattr(product, "primary_image_url", None)
        or ctx.default_og_image
    )
    og_image = absolute_url(ctx=ctx, path=primary_image) if primary_image else None
    in_stock = bool(getattr(product, "in_stock", True))
    price = getattr(product, "price", None)
    currency = getattr(product, "currency", "BDT") or "BDT"
    sku = getattr(product, "sku", None) or f"HS-{getattr(product, 'id', '')}"
    brand_obj = getattr(product, "brand", None)
    brand_name = getattr(brand_obj, "name", None) if brand_obj else None

    product_schema: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": JSONLD_TYPE_PRODUCT,
        "name": name,
        "description": description,
        "sku": sku,
        "url": canonical,
    }
    if og_image:
        product_schema["image"] = [og_image]
    if brand_name:
        product_schema["brand"] = {"@type": "Brand", "name": brand_name}
    if price is not None:
        product_schema["offers"] = {
            "@type": JSONLD_TYPE_OFFER,
            "priceCurrency": currency,
            "price": _decimal_str(price),
            "availability": (
                AVAILABILITY_IN_STOCK if in_stock
                else AVAILABILITY_OUT_OF_STOCK
            ),
            "url": canonical,
        }
    rating = getattr(product, "rating", None)
    review_count = getattr(product, "review_count", None)
    if rating is not None and review_count and int(review_count) > 0:
        product_schema["aggregateRating"] = {
            "@type": JSONLD_TYPE_AGGREGATE_RATING,
            "ratingValue": float(rating),
            "reviewCount": int(review_count),
        }

    crumbs = breadcrumb_trail or [
        ("Home", "/"),
        ("Products", "/products"),
        (name, f"/product/{slug}"),
    ]
    jsonld: list[dict[str, Any]] = [
        organization_jsonld(ctx),
        breadcrumb_jsonld(ctx, crumbs),
        product_schema,
    ]
    if faqs:
        jsonld.append(faqpage_jsonld(faqs))
    return SeoBundle(
        title=f"{name} | {ctx.site_name}",
        meta_title=f"{name} | {ctx.site_name}",
        meta_description=description,
        canonical_url=canonical,
        og_type=OgType.PRODUCT.value,
        og_image=og_image,
        og_url=canonical,
        twitter_card=ctx.default_twitter_card,
        jsonld=jsonld,
    )


def build_category_meta(
    *,
    ctx: SiteContext,
    category: Any,
    breadcrumb_trail: list[tuple[str, str]] | None = None,
    faqs: list[tuple[str, str]] | None = None,
) -> SeoBundle:
    name = getattr(category, "name", "") or ""
    slug = getattr(category, "slug", None) or str(
        getattr(category, "id", ""),
    )
    description = truncate_description(
        getattr(category, "description", None)
        or f"Shop {name} at {ctx.site_name}. Genuine products, fast "
           f"delivery in Bangladesh.",
    )
    canonical = absolute_url(ctx=ctx, path=f"/category/{slug}")
    og_image = (
        absolute_url(ctx=ctx, path=ctx.default_og_image)
        if ctx.default_og_image else None
    )
    crumbs = breadcrumb_trail or [
        ("Home", "/"),
        ("Categories", "/categories"),
        (name, f"/category/{slug}"),
    ]
    jsonld: list[dict[str, Any]] = [
        organization_jsonld(ctx),
        breadcrumb_jsonld(ctx, crumbs),
    ]
    if faqs:
        jsonld.append(faqpage_jsonld(faqs))
    return SeoBundle(
        title=f"{name} | {ctx.site_name}",
        meta_title=f"{name} | {ctx.site_name}",
        meta_description=description,
        canonical_url=canonical,
        og_type=OgType.WEBSITE.value,
        og_image=og_image,
        og_url=canonical,
        twitter_card=ctx.default_twitter_card,
        jsonld=jsonld,
    )


def build_brand_meta(
    *,
    ctx: SiteContext,
    brand: Any,
    breadcrumb_trail: list[tuple[str, str]] | None = None,
) -> SeoBundle:
    name = getattr(brand, "name", "") or ""
    slug = getattr(brand, "slug", None) or str(getattr(brand, "id", ""))
    description = truncate_description(
        getattr(brand, "description", None)
        or f"{name} products at {ctx.site_name}.",
    )
    canonical = absolute_url(ctx=ctx, path=f"/brand/{slug}")
    og_image = (
        absolute_url(ctx=ctx, path=ctx.default_og_image)
        if ctx.default_og_image else None
    )
    crumbs = breadcrumb_trail or [
        ("Home", "/"),
        ("Brands", "/brands"),
        (name, f"/brand/{slug}"),
    ]
    return SeoBundle(
        title=f"{name} | {ctx.site_name}",
        meta_title=f"{name} | {ctx.site_name}",
        meta_description=description,
        canonical_url=canonical,
        og_type=OgType.WEBSITE.value,
        og_image=og_image,
        og_url=canonical,
        twitter_card=ctx.default_twitter_card,
        jsonld=[
            organization_jsonld(ctx),
            breadcrumb_jsonld(ctx, crumbs),
        ],
    )


def build_blog_post_meta(
    *,
    ctx: SiteContext,
    post: Any,
) -> SeoBundle:
    """Build SEO bundle for a BlogPost (Module 34 own model).

    Adds a JSON-LD ``BlogPosting`` schema for richer search snippets.
    """
    title = getattr(post, "title", "") or ""
    excerpt = getattr(post, "excerpt", None) or title
    slug = getattr(post, "slug", "")
    description = truncate_description(excerpt)
    canonical = absolute_url(ctx=ctx, path=f"/blog/{slug}")
    cover = getattr(post, "cover_image_url", None)
    og_image = (
        absolute_url(ctx=ctx, path=cover) if cover
        else absolute_url(ctx=ctx, path=ctx.default_og_image) if ctx.default_og_image
        else None
    )
    published_at = getattr(post, "published_at", None)

    blog_posting: dict[str, Any] = {
        "@context": SCHEMA_CONTEXT,
        "@type": JSONLD_TYPE_BLOG_POSTING,
        "headline": title,
        "description": description,
        "url": canonical,
    }
    if og_image:
        blog_posting["image"] = [og_image]
    if published_at:
        blog_posting["datePublished"] = (
            published_at.isoformat()
            if isinstance(published_at, datetime)
            else str(published_at)
        )
    author_name = getattr(post, "author_name", None)
    if author_name:
        blog_posting["author"] = {"@type": "Person", "name": author_name}

    return SeoBundle(
        title=f"{title} | {ctx.site_name}",
        meta_title=f"{title} | {ctx.site_name}",
        meta_description=description,
        canonical_url=canonical,
        og_type=OgType.ARTICLE.value,
        og_image=og_image,
        og_url=canonical,
        twitter_card=ctx.default_twitter_card,
        jsonld=[
            organization_jsonld(ctx),
            breadcrumb_jsonld(ctx, [
                ("Home", "/"),
                ("Blog", "/blog"),
                (title, f"/blog/{slug}"),
            ]),
            blog_posting,
        ],
    )


def build_static_page_meta(
    *,
    ctx: SiteContext,
    slug: str,
    title: str,
    description: str,
    breadcrumb_trail: list[tuple[str, str]] | None = None,
) -> SeoBundle:
    canonical = absolute_url(ctx=ctx, path=f"/{slug.lstrip('/')}")
    og_image = (
        absolute_url(ctx=ctx, path=ctx.default_og_image)
        if ctx.default_og_image else None
    )
    crumbs = breadcrumb_trail or [
        ("Home", "/"),
        (title, f"/{slug.lstrip('/')}"),
    ]
    return SeoBundle(
        title=f"{title} | {ctx.site_name}",
        meta_title=f"{title} | {ctx.site_name}",
        meta_description=truncate_description(description),
        canonical_url=canonical,
        og_type=OgType.WEBSITE.value,
        og_image=og_image,
        og_url=canonical,
        twitter_card=ctx.default_twitter_card,
        jsonld=[
            organization_jsonld(ctx),
            breadcrumb_jsonld(ctx, crumbs),
        ],
    )


# ----------------------------------------------------------------------
# Override merger
# ----------------------------------------------------------------------
def apply_override(
    bundle: SeoBundle,
    *,
    override: Any | None,
) -> SeoBundle:
    """Overlay a SeoMetaOverride row onto a built bundle.

    Any non-NULL field on the override wins. Extra meta + extra JSON-LD
    are MERGED (not replaced) so admin can add to the auto-generated
    set without losing it.
    """
    if override is None:
        return bundle

    if override.title is not None:
        bundle.title = override.title
        bundle.meta_title = override.title
    if override.meta_description is not None:
        bundle.meta_description = override.meta_description
    if override.canonical_url is not None:
        bundle.canonical_url = override.canonical_url
        bundle.og_url = override.canonical_url
    if override.og_image_url is not None:
        bundle.og_image = override.og_image_url
    if override.og_type is not None:
        bundle.og_type = override.og_type
    if override.twitter_card is not None:
        bundle.twitter_card = override.twitter_card
    if override.robots_directives is not None:
        bundle.robots = override.robots_directives
    if override.extra_meta_json:
        bundle.extra_meta = {**bundle.extra_meta, **override.extra_meta_json}
    if override.extra_jsonld_json:
        bundle.jsonld = [*bundle.jsonld, *override.extra_jsonld_json]
    return bundle


# ----------------------------------------------------------------------
# Translation merger — Module 34 multi-language sprint
# ----------------------------------------------------------------------
def apply_translation(
    bundle: SeoBundle,
    *,
    translation: Any | None,
) -> SeoBundle:
    """Overlay a SeoMetaTranslation row onto a built bundle.

    Sits ABOVE ``apply_override`` in the resolution chain — call order:
        bundle = build_*_meta(...)
        bundle = apply_override(bundle, override=override)
        bundle = apply_translation(bundle, translation=translation)

    Non-NULL fields win; NULL fields fall through to the override / default.
    ``og_title`` / ``og_description`` are surfaced via extra_meta because
    OG title/description aren't first-class fields on SeoBundle (they're
    rendered by the frontend from these meta keys).
    ``keywords`` is added to extra_meta as the canonical "keywords" key.
    """
    if translation is None:
        return bundle

    if translation.title is not None:
        bundle.title = translation.title
        bundle.meta_title = translation.title
    if translation.meta_description is not None:
        bundle.meta_description = translation.meta_description
    extra_updates: dict[str, str] = {}
    if translation.og_title is not None:
        extra_updates["og:title"] = translation.og_title
    if translation.og_description is not None:
        extra_updates["og:description"] = translation.og_description
    if translation.keywords is not None:
        extra_updates["keywords"] = translation.keywords
    if extra_updates:
        bundle.extra_meta = {**bundle.extra_meta, **extra_updates}
    return bundle


def hreflang_links(
    *,
    canonical_url: str,
    default_locale: str,
    available_locales: list[str],
) -> list[dict[str, str]]:
    """Build a list of hreflang link entries the frontend renders as:

        <link rel="alternate" hreflang="en"  href="<url>" />
        <link rel="alternate" hreflang="bn"  href="<url>?lang=bn" />
        <link rel="alternate" hreflang="x-default" href="<url>" />

    The default locale's URL is the bare canonical (no ``?lang=`` query
    param — Google treats that as the x-default candidate). Other locales
    append ``?lang=<code>``. If the canonical already has a query string,
    we append with ``&``.

    ``available_locales`` is what's actually in the DB for this entity;
    we always include the default in the list even if no translation row
    exists (the default IS the bare URL).
    """
    seen: set[str] = set()
    entries: list[dict[str, str]] = []

    def url_for(locale: str) -> str:
        if locale == default_locale:
            return canonical_url
        sep = "&" if "?" in canonical_url else "?"
        return f"{canonical_url}{sep}lang={locale}"

    # Always emit the default first.
    entries.append({"hreflang": default_locale, "href": url_for(default_locale)})
    seen.add(default_locale)

    for loc in available_locales:
        if loc in seen:
            continue
        entries.append({"hreflang": loc, "href": url_for(loc)})
        seen.add(loc)

    # x-default fallback always points at the default-locale URL.
    entries.append({"hreflang": "x-default", "href": url_for(default_locale)})
    return entries


# ----------------------------------------------------------------------
# Decimal helpers
# ----------------------------------------------------------------------
def _decimal_str(v: Any) -> str:
    """Format a price for JSON-LD (string per schema.org spec)."""
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    try:
        return f"{Decimal(str(v)):.2f}"
    except Exception:  # noqa: BLE001
        return str(v)
