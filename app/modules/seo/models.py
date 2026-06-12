"""ORM models for the SEO + dynamic-content module (Module 34).

Four new tables:

  seo_meta_overrides     — per-(entity_type, entity_key) override of
                           the auto-generated SEO bundle. The
                           ``builders`` module merges these on top of
                           the entity's default-derived meta.
  homepage_banners       — admin-managed banner carousel for the
                           homepage; surfaced via /api/v1/seo/banners.
  blog_posts             — markdown blog content with slug routing
                           + JSON-LD BlogPosting schema generation.
  url_redirects          — 301/302 redirect map served by the
                           public ``/r/<path>`` endpoint.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


# ============================================================
#  seo_meta_overrides
# ============================================================
class SeoMetaOverride(Base, TimestampMixin):
    """Per-(entity_type × entity_key) admin override of SEO meta.

    ``entity_key`` is a string so it can hold either a UUID hex (for
    catalog rows) or a slug (for static pages like "home", "about").
    Composite UNIQUE on (entity_type, entity_key).

    Any field left NULL falls back to the auto-generated default in
    ``builders``. This way admins only override what they care about.
    """

    __tablename__ = "seo_meta_overrides"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # SeoEntityType — see state.py + DB CHECK below.
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(160), nullable=False)

    # The override fields (all optional — NULL means use the default).
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(
        String(320), nullable=True,  # Google truncates around 160 but
                                     # allow up to 320 to support
                                     # different SERP contexts
    )
    canonical_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
    )
    og_image_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
    )
    og_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    twitter_card: Mapped[str | None] = mapped_column(
        String(24), nullable=True,
    )
    # robots directives — comma-separated list of "noindex", "nofollow",
    # "max-snippet:-1", etc. NULL = let crawlers index normally.
    robots_directives: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    # Free-form additional <meta> tags rendered by the frontend.
    # Example: {"keywords": "paracetamol, fever, BD pharmacy"}
    extra_meta_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    # Free-form JSON-LD blocks to APPEND to the auto-generated ones.
    # Use sparingly — most product/category pages don't need extras.
    extra_jsonld_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )

    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Auto-SEO engine flag (alembic 0079). True = SeoAutoGenService owns
    # this row + future backfills may refresh. False = manual override,
    # engine will never touch.
    auto_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )

    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_key",
            name="uq_seo_overrides_type_key",
        ),
        CheckConstraint(
            "entity_type IN ('product','category','brand',"
            "'blog_post','static_page')",
            name="ck_seo_overrides_entity_type_enum",
        ),
        CheckConstraint(
            "og_type IS NULL OR og_type IN ('website','product','article')",
            name="ck_seo_overrides_og_type_enum",
        ),
        CheckConstraint(
            "twitter_card IS NULL OR "
            "twitter_card IN ('summary','summary_large_image')",
            name="ck_seo_overrides_twitter_card_enum",
        ),
    )


# ============================================================
#  seo_meta_translations  — Module 34 multi-language sprint
# ============================================================
class SeoMetaTranslation(Base, TimestampMixin):
    """Per-(entity_type × entity_key × locale) translation row.

    Sits BELOW ``SeoMetaOverride`` in the resolution chain:
        translation[locale]   →  override   →  builder default

    Each translation column is nullable so operators only fill the
    fields they actually translate (e.g. just title + meta_description
    for the bn locale, letting OG fields fall back to en).
    """

    __tablename__ = "seo_meta_translations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(160), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(
        String(320), nullable=True,
    )
    og_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    og_description: Mapped[str | None] = mapped_column(
        String(320), nullable=True,
    )
    keywords: Mapped[str | None] = mapped_column(String(500), nullable=True)

    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Auto-SEO engine flag (alembic 0079). True = SeoAutoGenService owns
    # this row + future backfills may refresh. False = manual override,
    # engine will never touch.
    auto_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )

    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_key", "locale",
            name="uq_seo_translations_type_key_locale",
        ),
        CheckConstraint(
            "entity_type IN ('product','category','brand',"
            "'blog_post','static_page')",
            name="ck_seo_translations_entity_type_enum",
        ),
        CheckConstraint(
            "locale IN ('en','bn')",
            name="ck_seo_translations_locale_enum",
        ),
        Index(
            "ix_seo_translations_lookup",
            "entity_type", "entity_key", "locale",
        ),
    )


# ============================================================
#  homepage_banners
# ============================================================
class HomepageBanner(Base, TimestampMixin):
    """Admin-managed banner carousel for the storefront homepage.

    Sorted by ``sort_order`` ASC; only ``is_active=true`` rows are
    surfaced via the public API. Optional ``valid_from`` / ``valid_until``
    let ops schedule promo banners ahead of time.
    """

    __tablename__ = "homepage_banners"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_url: Mapped[str] = mapped_column(String(512), nullable=False)
    # Mobile-only image (taller aspect). Optional — falls back to
    # ``image_url`` on mobile if NULL.
    mobile_image_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
    )
    target_url: Mapped[str] = mapped_column(String(512), nullable=False)
    # Optional alt text for screen readers + image-search.
    alt_text: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index(
            "ix_homepage_banners_active_sort",
            "is_active", "sort_order",
        ),
    )


# ============================================================
#  blog_posts
# ============================================================
class BlogPost(Base, TimestampMixin):
    """Markdown blog post with slug routing.

    Status: draft / published / archived. Only published posts appear
    in /sitemap.xml + /api/v1/seo/blog. ``slug`` is unique across all
    statuses to prevent collisions when re-publishing.
    """

    __tablename__ = "blog_posts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    slug: Mapped[str] = mapped_column(
        String(160), nullable=False, unique=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    cover_image_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
    )
    author_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    author_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # BlogPostStatus enum.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Tags for filtering — comma-separated. Kept simple v1; full
    # tag taxonomy is out of scope.
    tags_csv: Mapped[str] = mapped_column(
        String(500), nullable=False, server_default="",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_blog_posts_status_enum",
        ),
        Index(
            "ix_blog_posts_status_published",
            "status", "published_at",
        ),
    )


# ============================================================
#  url_redirects
# ============================================================
class UrlRedirect(Base, TimestampMixin):
    """301/302 redirect map served by the public ``/r/<path>`` endpoint.

    ``from_path`` is matched verbatim (no regex/wildcard support v1 —
    keep it predictable). Always store paths with leading ``/``.
    """

    __tablename__ = "seo_url_redirects"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    from_path: Mapped[str] = mapped_column(
        String(512), nullable=False, unique=True,
    )
    to_path: Mapped[str] = mapped_column(String(512), nullable=False)
    # RedirectType enum.
    redirect_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="permanent",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    # Last-hit cache for ops dashboards. Updated lazily on lookup.
    last_hit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "redirect_type IN ('permanent','temporary')",
            name="ck_url_redirects_type_enum",
        ),
        CheckConstraint(
            "from_path != to_path",
            name="ck_url_redirects_no_loop",
        ),
        CheckConstraint(
            "from_path LIKE '/%'",
            name="ck_url_redirects_from_starts_slash",
        ),
        Index(
            "ix_url_redirects_active",
            "is_active", "from_path",
        ),
    )


# ============================================================
#  entity_faqs  — per-(entity_type × entity_key) Q&A driving FAQPage JSON-LD
# ============================================================
class EntityFaq(Base, TimestampMixin):
    """Admin-curated FAQ entry emitted as ``FAQPage`` JSON-LD on the
    matching entity page (product / category / brand).

    Keyed by ``(entity_type, entity_key)`` — same convention as
    ``SeoMetaOverride`` / ``SeoMetaTranslation`` — so one table backs FAQs
    for every entity kind. ``entity_key`` holds a UUID hex (catalog rows)
    or a slug (static pages). Locale-scoped (``en``/``bn``); the bundle
    builders fall back to the default locale when the requested one has
    no rows. Only ``is_active=true`` rows render, ordered by ``position``
    then creation.
    """

    __tablename__ = "entity_faqs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(160), nullable=False)
    locale: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="en",
    )
    question: Mapped[str] = mapped_column(String(300), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('product','category','brand',"
            "'blog_post','static_page')",
            name="ck_entity_faqs_entity_type_enum",
        ),
        CheckConstraint(
            "locale IN ('en','bn')",
            name="ck_entity_faqs_locale_enum",
        ),
        Index(
            "ix_entity_faqs_lookup",
            "entity_type", "entity_key", "locale", "is_active", "position",
        ),
    )
