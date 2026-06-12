"""0077 entity_faqs — per-(entity_type x entity_key) Q&A driving FAQPage JSON-LD.

Admin-curated, locale-scoped (en/bn) Q&A rows keyed by
``(entity_type, entity_key)`` — same convention as seo_meta_overrides.
``SeoBundleService.for_product`` / ``for_category`` render them as an
FAQPage JSON-LD block (rich-result eligible). Only ``is_active`` rows
render, ordered by ``position`` then creation.

NOTE: this tree (``_serve_final``) chains FAQ after 0076; the v7 golive
tree carries the equivalent table as ``0081_product_faqs`` (after its
0080 head). The two are parallel deployment artifacts — do not merge the
migration chains.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0077_product_faqs"
down_revision: str | Sequence[str] | None = "0076_bulk_upload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_faqs",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("entity_key", sa.String(length=160), nullable=False),
        sa.Column(
            "locale", sa.String(length=8), nullable=False,
            server_default="en",
        ),
        sa.Column("question", sa.String(length=300), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column(
            "position", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "entity_type IN ('product','category','brand',"
            "'blog_post','static_page')",
            name="ck_entity_faqs_entity_type_enum",
        ),
        sa.CheckConstraint(
            "locale IN ('en','bn')",
            name="ck_entity_faqs_locale_enum",
        ),
    )
    op.create_index(
        "ix_entity_faqs_lookup",
        "entity_faqs",
        ["entity_type", "entity_key", "locale", "is_active", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_entity_faqs_lookup", table_name="entity_faqs")
    op.drop_table("entity_faqs")
