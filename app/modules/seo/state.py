"""Enums for the SEO + dynamic-content module.

Module 34 ships:
  - per-entity SEO overrides (product / category / brand / blog post /
    static page) — admin can override the auto-generated title /
    meta_description / canonical / OG image
  - homepage banners — admin-managed carousel
  - blog posts — markdown content with slug routing
  - URL redirects — 301/302 map for SEO-safe URL changes

Hypershop is API-only — these endpoints return JSON SEO bundles +
JSON-LD structured data that the frontend renders into ``<head>``.
``/sitemap.xml`` and ``/robots.txt`` are served raw from the API at
the public root for crawlers.
"""

from __future__ import annotations

from enum import StrEnum


class SeoEntityType(StrEnum):
    """What the override row is keyed against.

    Used as the discriminator on ``seo_meta_overrides``. Static-page
    overrides use ``slug`` (e.g. "home", "deals", "about") instead of
    a UUID entity_id.
    """

    PRODUCT = "product"
    CATEGORY = "category"
    BRAND = "brand"
    BLOG_POST = "blog_post"
    STATIC_PAGE = "static_page"


ALL_SEO_ENTITY_TYPES: frozenset[str] = frozenset(
    e.value for e in SeoEntityType
)


class OgType(StrEnum):
    """Open Graph type — controls how social shares render.

    Default is "website" for catalog/listing pages. "product" tag is
    Facebook's product-card type. "article" applies to blog posts.
    """

    WEBSITE = "website"
    PRODUCT = "product"
    ARTICLE = "article"


class TwitterCardType(StrEnum):
    SUMMARY = "summary"
    SUMMARY_LARGE_IMAGE = "summary_large_image"


class RedirectType(StrEnum):
    """HTTP redirect status code semantics.

    PERMANENT (301) — old URL is dead forever; search engines transfer
                      ranking to the new URL.
    TEMPORARY (302) — old URL will come back; do not transfer ranking.
    """

    PERMANENT = "permanent"   # → 301
    TEMPORARY = "temporary"   # → 302


REDIRECT_STATUS_CODE: dict[str, int] = {
    RedirectType.PERMANENT.value: 301,
    RedirectType.TEMPORARY.value: 302,
}


class BlogPostStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


ALL_BLOG_POST_STATUSES: frozenset[str] = frozenset(
    s.value for s in BlogPostStatus
)


# ----------------------------------------------------------------------
# Locale constants — Module 34 multi-language sprint
# ----------------------------------------------------------------------
DEFAULT_LOCALE: str = "en"
SUPPORTED_LOCALES: tuple[str, ...] = ("en", "bn")
SUPPORTED_LOCALES_SET: frozenset[str] = frozenset(SUPPORTED_LOCALES)


def normalise_locale(raw: str | None) -> str:
    """Normalise a request-provided locale.

    Falls back to ``DEFAULT_LOCALE`` for None / empty / unsupported.
    Accepts short codes ("en"), regional variants ("en-US" → "en"),
    and is case-insensitive ("BN" → "bn").
    """
    if not raw:
        return DEFAULT_LOCALE
    code = raw.strip().lower().split("-", 1)[0]
    if code in SUPPORTED_LOCALES_SET:
        return code
    return DEFAULT_LOCALE


# ----------------------------------------------------------------------
# JSON-LD schema.org type constants
# ----------------------------------------------------------------------
SCHEMA_CONTEXT = "https://schema.org"

JSONLD_TYPE_ORGANIZATION = "OnlineStore"  # subtype of OnlineBusiness/LocalBusiness
JSONLD_TYPE_PRODUCT = "Product"
JSONLD_TYPE_BREADCRUMB_LIST = "BreadcrumbList"
JSONLD_TYPE_LIST_ITEM = "ListItem"
JSONLD_TYPE_OFFER = "Offer"
JSONLD_TYPE_BLOG_POSTING = "BlogPosting"
JSONLD_TYPE_AGGREGATE_RATING = "AggregateRating"
JSONLD_TYPE_FAQ_PAGE = "FAQPage"
JSONLD_TYPE_QUESTION = "Question"
JSONLD_TYPE_ANSWER = "Answer"


# Schema.org availability URIs.
AVAILABILITY_IN_STOCK = "https://schema.org/InStock"
AVAILABILITY_OUT_OF_STOCK = "https://schema.org/OutOfStock"
AVAILABILITY_PREORDER = "https://schema.org/PreOrder"
