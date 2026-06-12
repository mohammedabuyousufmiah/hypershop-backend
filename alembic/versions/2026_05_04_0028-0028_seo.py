"""SEO + dynamic content — 4 tables.

Revision ID: 0028_seo
Revises: 0027_supplier_payments
Create Date: 2026-05-04

Module 34. See ``app/modules/seo/models.py`` for table-by-table
docstrings.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_seo"
down_revision: str | Sequence[str] | None = "0027_supplier_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- seo_meta_overrides ----------------
    op.create_table(
        "seo_meta_overrides",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("entity_key", sa.String(160), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("meta_description", sa.String(320), nullable=True),
        sa.Column("canonical_url", sa.String(512), nullable=True),
        sa.Column("og_image_url", sa.String(512), nullable=True),
        sa.Column("og_type", sa.String(16), nullable=True),
        sa.Column("twitter_card", sa.String(24), nullable=True),
        sa.Column("robots_directives", sa.String(255), nullable=True),
        sa.Column(
            "extra_meta_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "extra_jsonld_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint(
            "entity_type", "entity_key",
            name="uq_seo_overrides_type_key",
        ),
        sa.CheckConstraint(
            "entity_type IN ('product','category','brand',"
            "'blog_post','static_page')",
            name="ck_seo_overrides_entity_type_enum",
        ),
        sa.CheckConstraint(
            "og_type IS NULL OR og_type IN ('website','product','article')",
            name="ck_seo_overrides_og_type_enum",
        ),
        sa.CheckConstraint(
            "twitter_card IS NULL OR "
            "twitter_card IN ('summary','summary_large_image')",
            name="ck_seo_overrides_twitter_card_enum",
        ),
    )

    # ---------------- homepage_banners ----------------
    op.create_table(
        "homepage_banners",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("subtitle", sa.String(500), nullable=True),
        sa.Column("image_url", sa.String(512), nullable=False),
        sa.Column("mobile_image_url", sa.String(512), nullable=True),
        sa.Column("target_url", sa.String(512), nullable=False),
        sa.Column("alt_text", sa.String(255), nullable=True),
        sa.Column(
            "is_active", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_homepage_banners_active_sort",
        "homepage_banners",
        ["is_active", "sort_order"],
    )

    # ---------------- blog_posts ----------------
    op.create_table(
        "blog_posts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(160), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("excerpt", sa.String(500), nullable=True),
        sa.Column("body_markdown", sa.Text, nullable=False),
        sa.Column("cover_image_url", sa.String(512), nullable=True),
        sa.Column("author_name", sa.String(160), nullable=True),
        sa.Column(
            "author_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(16),
            nullable=False, server_default="draft",
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tags_csv", sa.String(500),
            nullable=False, server_default="",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_blog_posts_status_enum",
        ),
    )
    op.create_index(
        "ix_blog_posts_status_published",
        "blog_posts",
        ["status", "published_at"],
    )

    # ---------------- seo_url_redirects ----------------
    op.create_table(
        "seo_url_redirects",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("from_path", sa.String(512), nullable=False, unique=True),
        sa.Column("to_path", sa.String(512), nullable=False),
        sa.Column(
            "redirect_type", sa.String(16),
            nullable=False, server_default="permanent",
        ),
        sa.Column(
            "is_active", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "hit_count", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "redirect_type IN ('permanent','temporary')",
            name="ck_url_redirects_type_enum",
        ),
        sa.CheckConstraint(
            "from_path != to_path",
            name="ck_url_redirects_no_loop",
        ),
        sa.CheckConstraint(
            "from_path LIKE '/%'",
            name="ck_url_redirects_from_starts_slash",
        ),
    )
    op.create_index(
        "ix_url_redirects_active",
        "seo_url_redirects",
        ["is_active", "from_path"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_url_redirects_active", table_name="seo_url_redirects",
    )
    op.drop_table("seo_url_redirects")
    op.drop_index(
        "ix_blog_posts_status_published", table_name="blog_posts",
    )
    op.drop_table("blog_posts")
    op.drop_index(
        "ix_homepage_banners_active_sort", table_name="homepage_banners",
    )
    op.drop_table("homepage_banners")
    op.drop_table("seo_meta_overrides")
