"""storefront_cms — nav_items + featured_categories + static_pages

Three small tables that drive the live storefront layout:

- ``storefront_nav_items``     — top-nav links (label/href/sort_order/locale)
- ``storefront_featured``      — homepage featured category cards
- ``storefront_static_pages``  — about/terms/privacy/etc., rendered at
                                 /pages/<slug>

HomepageBanner already lives in the seo module (created with module
34); kept there to avoid a destructive migration. The unified
``/storefront/layout`` endpoint joins across modules.

Revision ID: 0078
Revises: 0077
Create Date: 2026-05-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0078_storefront_cms"
down_revision = "0077_product_faqs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storefront_nav_items",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("label_en", sa.String(80), nullable=False),
        sa.Column("label_bn", sa.String(80), nullable=True),
        sa.Column("href", sa.String(255), nullable=False),
        sa.Column("icon", sa.String(40), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.true(),
        ),
        sa.Column("open_in_new_tab", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_storefront_nav_items_active_sort",
        "storefront_nav_items",
        ["is_active", "sort_order"],
    )

    op.create_table(
        "storefront_featured_categories",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("category_slug", sa.String(120), nullable=False),
        sa.Column("display_label_en", sa.String(80), nullable=True),
        sa.Column("display_label_bn", sa.String(80), nullable=True),
        sa.Column("image_url", sa.String(512), nullable=True),
        sa.Column("badge_text", sa.String(40), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.true(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "category_slug", name="uq_featured_categories_slug",
        ),
    )
    op.create_index(
        "ix_storefront_featured_active_sort",
        "storefront_featured_categories",
        ["is_active", "sort_order"],
    )

    op.create_table(
        "storefront_static_pages",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("title_en", sa.String(160), nullable=False),
        sa.Column("title_bn", sa.String(160), nullable=True),
        sa.Column("body_md_en", sa.Text, nullable=False),
        sa.Column("body_md_bn", sa.Text, nullable=True),
        sa.Column("meta_description", sa.String(255), nullable=True),
        sa.Column(
            "is_published", sa.Boolean, nullable=False, server_default=sa.true(),
        ),
        sa.Column(
            "show_in_footer", sa.Boolean, nullable=False, server_default=sa.true(),
        ),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_storefront_static_pages_published_sort",
        "storefront_static_pages",
        ["is_published", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_storefront_static_pages_published_sort", table_name="storefront_static_pages")
    op.drop_table("storefront_static_pages")
    op.drop_index("ix_storefront_featured_active_sort", table_name="storefront_featured_categories")
    op.drop_table("storefront_featured_categories")
    op.drop_index("ix_storefront_nav_items_active_sort", table_name="storefront_nav_items")
    op.drop_table("storefront_nav_items")
