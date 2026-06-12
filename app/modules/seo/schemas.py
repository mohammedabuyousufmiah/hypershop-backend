"""Pydantic v2 wire schemas for the SEO module."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel  # noqa: F401  (used by all schemas below)


# ============================================================
#  Public bundle response
# ============================================================
class SeoBundleResponse(StrictModel):
    title: str
    meta_title: str
    meta_description: str
    canonical_url: str
    og_type: str
    og_image: str | None
    og_url: str
    twitter_card: str
    robots: str | None = None
    extra_meta: dict[str, str] = Field(default_factory=dict)
    jsonld: list[dict[str, Any]] = Field(default_factory=list)
    # Module 34 multi-language sprint
    locale: str = "en"
    hreflang: list[dict[str, str]] = Field(default_factory=list)


# ============================================================
#  Banners
# ============================================================
class BannerCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    subtitle: str | None = Field(default=None, max_length=500)
    image_url: str = Field(min_length=1, max_length=512)
    mobile_image_url: str | None = Field(default=None, max_length=512)
    target_url: str = Field(min_length=1, max_length=512)
    alt_text: str | None = Field(default=None, max_length=255)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class BannerUpdateRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    subtitle: str | None = Field(default=None, max_length=500)
    image_url: str | None = Field(default=None, min_length=1, max_length=512)
    mobile_image_url: str | None = Field(default=None, max_length=512)
    target_url: str | None = Field(default=None, min_length=1, max_length=512)
    alt_text: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class BannerResponse(StrictModel):
    id: UUID
    title: str
    subtitle: str | None
    image_url: str
    mobile_image_url: str | None
    target_url: str
    alt_text: str | None
    is_active: bool
    sort_order: int
    valid_from: datetime | None
    valid_until: datetime | None


class BannerListResponse(StrictModel):
    items: list[BannerResponse]


# ============================================================
#  Blog posts
# ============================================================
class BlogPostCreateRequest(StrictModel):
    slug: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9\-]+$")
    title: str = Field(min_length=1, max_length=255)
    excerpt: str | None = Field(default=None, max_length=500)
    body_markdown: str = Field(min_length=1)
    cover_image_url: str | None = Field(default=None, max_length=512)
    author_name: str | None = Field(default=None, max_length=160)
    status: Literal["draft", "published", "archived"] = "draft"
    tags_csv: str = Field(default="", max_length=500)


class BlogPostUpdateRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    excerpt: str | None = Field(default=None, max_length=500)
    body_markdown: str | None = None
    cover_image_url: str | None = Field(default=None, max_length=512)
    author_name: str | None = Field(default=None, max_length=160)
    status: Literal["draft", "published", "archived"] | None = None
    tags_csv: str | None = Field(default=None, max_length=500)


class BlogPostResponse(StrictModel):
    id: UUID
    slug: str
    title: str
    excerpt: str | None
    body_markdown: str
    cover_image_url: str | None
    author_name: str | None
    status: str
    published_at: datetime | None
    tags_csv: str
    created_at: datetime
    updated_at: datetime


class BlogPostListItem(StrictModel):
    id: UUID
    slug: str
    title: str
    excerpt: str | None
    cover_image_url: str | None
    author_name: str | None
    status: str
    published_at: datetime | None
    tags_csv: str


class BlogPostListResponse(StrictModel):
    items: list[BlogPostListItem]


# ============================================================
#  SEO overrides
# ============================================================
class SeoOverrideUpsertRequest(StrictModel):
    entity_type: Literal[
        "product", "category", "brand", "blog_post", "static_page",
    ]
    entity_key: str = Field(min_length=1, max_length=160)
    title: str | None = Field(default=None, max_length=255)
    meta_description: str | None = Field(default=None, max_length=320)
    canonical_url: str | None = Field(default=None, max_length=512)
    og_image_url: str | None = Field(default=None, max_length=512)
    og_type: Literal["website", "product", "article"] | None = None
    twitter_card: Literal["summary", "summary_large_image"] | None = None
    robots_directives: str | None = Field(default=None, max_length=255)
    extra_meta_json: dict[str, str] | None = None
    extra_jsonld_json: list[dict[str, Any]] | None = None


class SeoOverrideResponse(StrictModel):
    id: UUID
    entity_type: str
    entity_key: str
    title: str | None
    meta_description: str | None
    canonical_url: str | None
    og_image_url: str | None
    og_type: str | None
    twitter_card: str | None
    robots_directives: str | None
    extra_meta: dict[str, Any]
    extra_jsonld: list[dict[str, Any]]


# ============================================================
#  Translations  — Module 34 multi-language sprint
# ============================================================
class SeoTranslationUpsertRequest(StrictModel):
    entity_type: Literal[
        "product", "category", "brand", "blog_post", "static_page",
    ]
    entity_key: str = Field(min_length=1, max_length=160)
    locale: Literal["en", "bn"]
    title: str | None = Field(default=None, max_length=255)
    meta_description: str | None = Field(default=None, max_length=320)
    og_title: str | None = Field(default=None, max_length=255)
    og_description: str | None = Field(default=None, max_length=320)
    keywords: str | None = Field(default=None, max_length=500)


class SeoTranslationResponse(StrictModel):
    id: UUID
    entity_type: str
    entity_key: str
    locale: str
    title: str | None
    meta_description: str | None
    og_title: str | None
    og_description: str | None
    keywords: str | None
    created_at: datetime
    updated_at: datetime


class SeoTranslationListResponse(StrictModel):
    items: list[SeoTranslationResponse]


# ============================================================
#  Redirects
# ============================================================
class RedirectCreateRequest(StrictModel):
    from_path: str = Field(min_length=2, max_length=512)
    to_path: str = Field(min_length=1, max_length=512)
    redirect_type: Literal["permanent", "temporary"] = "permanent"
    note: str | None = Field(default=None, max_length=500)


class RedirectResponse(StrictModel):
    id: UUID
    from_path: str
    to_path: str
    redirect_type: str
    is_active: bool
    last_hit_at: datetime | None
    hit_count: int
    note: str | None


class RedirectListResponse(StrictModel):
    items: list[RedirectResponse]


# ============================================================
#  Product FAQs  — drive FAQPage JSON-LD
# ============================================================
class FaqCreateRequest(StrictModel):
    entity_type: Literal[
        "product", "category", "brand", "blog_post", "static_page",
    ]
    entity_key: str = Field(min_length=1, max_length=160)
    locale: Literal["en", "bn"] = "en"
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1)
    position: int = Field(default=0, ge=0)
    is_active: bool = True


class FaqUpdateRequest(StrictModel):
    locale: Literal["en", "bn"] | None = None
    question: str | None = Field(default=None, min_length=1, max_length=300)
    answer: str | None = Field(default=None, min_length=1)
    position: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class FaqResponse(StrictModel):
    id: UUID
    entity_type: str
    entity_key: str
    locale: str
    question: str
    answer: str
    position: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FaqListResponse(StrictModel):
    items: list[FaqResponse]


# ============================================================
#  FAQ bulk-import + AI-suggest (task #169, 2026-05-25)
# ============================================================
class FaqBulkItem(StrictModel):
    """One row in a bulk-import batch. ``locale`` and ``position`` carry
    sensible defaults so a CSV column-count of 4 (entity_type, entity_key,
    question, answer) is the minimum the operator needs to provide."""
    entity_type: Literal[
        "product", "category", "brand", "blog_post", "static_page",
    ]
    entity_key: str = Field(min_length=1, max_length=160)
    locale: Literal["en", "bn"] = "en"
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1)
    position: int = Field(default=0, ge=0)
    is_active: bool = True


class FaqBulkImportRequest(StrictModel):
    items: list[FaqBulkItem] = Field(min_length=1, max_length=500)
    skip_duplicates: bool = Field(
        default=True,
        description=(
            "When True, an entity_key+locale+question hash already in the "
            "table is silently skipped. When False, those rows fail the "
            "batch and the whole import rolls back."
        ),
    )


class FaqBulkImportResponse(StrictModel):
    requested: int
    created: int
    skipped: int
    failed: int
    errors: list[str] = Field(default_factory=list, max_length=20)


class FaqSuggestRequest(StrictModel):
    entity_type: Literal[
        "product", "category", "brand", "blog_post", "static_page",
    ]
    entity_key: str = Field(min_length=1, max_length=160)
    locale: Literal["en", "bn"] = "en"
    max_items: int = Field(default=5, ge=1, le=12)


class FaqSuggestItem(StrictModel):
    question: str
    answer: str
    source: Literal["template", "llm"]


class FaqSuggestResponse(StrictModel):
    items: list[FaqSuggestItem]
    source: Literal["template", "llm"] = "template"
    notice: str | None = None


class CommercePolicyResponse(StrictModel):
    """Org-wide return + shipping policy values used by the PDP to
    build MerchantReturnPolicy + OfferShippingDetails JSON-LD without
    hitting the backend twice. All monetary values in minor units
    (BDT cents).

    Per-region shipping (Module post-#34 SEO push): the Dhaka rate +
    the outside-Dhaka rate are surfaced separately so the FE can emit
    two ``OfferShippingDetails`` rows with the right DefinedRegion.
    """

    return_days: int
    return_fees: str
    return_method: str
    shipping_flat_minor: int
    shipping_free_threshold_minor: int
    shipping_handling_min_days: int
    shipping_handling_max_days: int
    shipping_transit_min_days: int
    shipping_transit_max_days: int
    # Outside-Dhaka regional split.
    shipping_outside_flat_minor: int = 12000
    shipping_outside_free_threshold_minor: int = 199900
    shipping_outside_transit_min_days: int = 3
    shipping_outside_transit_max_days: int = 7
    shipping_dhaka_region: str = "Dhaka"
    # Districts that accept Cash on Delivery (drives Offer.eligibleRegion).
    # Empty = COD available country-wide.
    cod_eligible_districts: list[str] = []
    country_code: str = "BD"
    currency: str = "BDT"
    # Seasonal return-policy override — populated when a campaign
    # (Eid, Pohela Boishakh) extends the standard window. Empty name
    # = no active override.
    seasonal_name: str = ""
    seasonal_start: str = ""
    seasonal_end: str = ""
    seasonal_days: int = 0


class SiteConfigResponse(StrictModel):
    """Public read-only site config — used by the frontend to render
    consistent branding (header logo, default OG image, organization
    name, etc.).
    """

    site_name: str
    site_url: str
    default_og_image: str | None
    pharmacy_phone: str | None
    pharmacy_locality: str
    pharmacy_country: str
    organization_jsonld: dict[str, Any]
    commerce_policy: CommercePolicyResponse | None = None
    # Webmaster verification tokens — empty strings filtered out client-side.
    verify_google: str = ""
    verify_bing: str = ""
    verify_yandex: str = ""
    verify_naver: str = ""
    verify_seznam: str = ""
    verify_facebook: str = ""
    verify_pinterest: str = ""
