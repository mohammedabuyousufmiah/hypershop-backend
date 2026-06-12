"""auto-SEO engine: auto_generated flag on seo meta tables

Revision ID: 0079_seo_autogen_flag
Revises: 0078_storefront_cms
Create Date: 2026-05-24

Adds a boolean ``auto_generated`` to ``seo_meta_overrides`` and
``seo_meta_translations``. The auto-SEO engine (SeoAutoGenService) sets
it True on rows it produces; any manual admin edit flips it False so the
engine's backfill / regeneration never overwrites a human-curated row.
Defaults to false → all pre-existing rows are treated as manual.

Ported from v7 zip (was 0080_seo_autogen_flag depending on
0079_rider_kyc which doesn't exist in _serve_final). Renumbered to
0079 chained off 0078_storefront_cms.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0079_seo_autogen_flag"
down_revision = "0078_storefront_cms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "seo_meta_overrides",
        sa.Column(
            "auto_generated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "seo_meta_translations",
        sa.Column(
            "auto_generated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("seo_meta_translations", "auto_generated")
    op.drop_column("seo_meta_overrides", "auto_generated")
