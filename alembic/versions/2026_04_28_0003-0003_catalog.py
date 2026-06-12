"""Catalog: brands, categories, products, variants, media

Revision ID: 0003_catalog
Revises: 0002_iam
Create Date: 2026-04-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_catalog"
down_revision: str | Sequence[str] | None = "0002_iam"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("logo_url", sa.String(2048), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint("name", name="uq_brands_name"),
        sa.UniqueConstraint("slug", name="uq_brands_slug"),
    )

    op.create_table(
        "categories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("description", sa.String(2048), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint("parent_id", "slug", name="uq_categories_parent_slug"),
    )
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"])

    op.create_table(
        "products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_description", sa.String(512), nullable=True),
        sa.Column("description", sa.String(8192), nullable=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column(
            "tax_class",
            sa.String(32),
            nullable=False,
            server_default="standard",
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("search_text", sa.String(2048), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('draft','active','archived')",
            name="ck_products_status_enum",
        ),
        sa.CheckConstraint(
            "base_currency = upper(base_currency) AND length(base_currency) = 3",
            name="ck_products_base_currency_iso",
        ),
        sa.UniqueConstraint("slug", name="uq_products_slug"),
    )
    op.create_index("ix_products_status", "products", ["status"])
    op.create_index("ix_products_brand_id", "products", ["brand_id"])
    op.create_index("ix_products_category_id", "products", ["category_id"])

    op.create_table(
        "product_variants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column(
            "options",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("price", sa.Numeric(14, 2), nullable=False),
        sa.Column("compare_at_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("weight_grams", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint("price >= 0", name="ck_product_variants_price_nonneg"),
        sa.CheckConstraint(
            "compare_at_price IS NULL OR compare_at_price >= price",
            name="ck_product_variants_compare_at_ge_price",
        ),
        sa.CheckConstraint(
            "weight_grams IS NULL OR weight_grams >= 0",
            name="ck_product_variants_weight_nonneg",
        ),
        sa.UniqueConstraint("sku", name="uq_product_variants_sku"),
        sa.UniqueConstraint("barcode", name="uq_product_variants_barcode"),
    )
    op.create_index(
        "ix_product_variants_product_id", "product_variants", ["product_id"],
    )

    op.create_table(
        "product_media",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "kind",
            sa.String(16),
            nullable=False,
            server_default="image",
        ),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("alt", sa.String(255), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "kind IN ('image','video')",
            name="ck_product_media_kind_enum",
        ),
    )
    op.create_index(
        "ix_product_media_product_id", "product_media", ["product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_media_product_id", table_name="product_media")
    op.drop_table("product_media")

    op.drop_index("ix_product_variants_product_id", table_name="product_variants")
    op.drop_table("product_variants")

    op.drop_index("ix_products_category_id", table_name="products")
    op.drop_index("ix_products_brand_id", table_name="products")
    op.drop_index("ix_products_status", table_name="products")
    op.drop_table("products")

    op.drop_index("ix_categories_parent_id", table_name="categories")
    op.drop_table("categories")

    op.drop_table("brands")
