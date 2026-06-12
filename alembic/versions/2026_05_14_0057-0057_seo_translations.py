"""0057 seo_meta_translations — Module 34 multi-language sprint.

Adds a single table ``seo_meta_translations`` that carries per-locale
translations of SEO meta for each (entity_type, entity_key). The
service-layer locale resolution applies these on top of the
auto-generated bundle and any matching ``seo_meta_overrides`` row.

Rule of precedence (highest wins):
  1. seo_meta_translations[entity_type, entity_key, locale]
  2. seo_meta_overrides[entity_type, entity_key]
  3. auto-generated default from builders.build_*_meta(...)

A translation row that leaves a column NULL falls back to the override
or default — operators only need to fill the fields they actually
translate.

Locales:
  - en  (English, default)
  - bn  (Bangla / Bengali)

Adding more locales = drop the CHECK constraint or extend the allowed
list in a follow-up migration. The service layer's SUPPORTED_LOCALES
set is the source of truth for what the API accepts.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0057_seo_translations"
down_revision: str | Sequence[str] | None = "0056_funnel_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seo_meta_translations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("entity_key", sa.String(160), nullable=False),
        sa.Column("locale", sa.String(8), nullable=False),
        # Per-locale overrides (every column nullable — falls back to
        # base override or builder default when NULL).
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("meta_description", sa.String(320), nullable=True),
        sa.Column("og_title", sa.String(255), nullable=True),
        sa.Column("og_description", sa.String(320), nullable=True),
        sa.Column("keywords", sa.String(500), nullable=True),
        sa.Column(
            "updated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "entity_type", "entity_key", "locale",
            name="uq_seo_translations_type_key_locale",
        ),
        sa.CheckConstraint(
            "entity_type IN ('product','category','brand',"
            "'blog_post','static_page')",
            name="ck_seo_translations_entity_type_enum",
        ),
        sa.CheckConstraint(
            "locale IN ('en','bn')",
            name="ck_seo_translations_locale_enum",
        ),
    )
    op.create_index(
        "ix_seo_translations_lookup",
        "seo_meta_translations",
        ["entity_type", "entity_key", "locale"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_seo_translations_lookup",
        table_name="seo_meta_translations",
    )
    op.drop_table("seo_meta_translations")
