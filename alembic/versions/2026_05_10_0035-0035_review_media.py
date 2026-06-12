"""reviews phase 2 — review_media table for customer-uploaded review photos

Revision ID: 0035_review_media
Revises: 0034_products_seller_id
Create Date: 2026-05-10

Phase 2 of the reviews initiative. Single new table; no changes to
the phase-1 ``product_reviews`` row shape. Customers can attach up to
``MAX_MEDIA_PER_REVIEW`` images (defined in ``app.modules.reviews.codes``)
to their review. Visibility is gated by the parent review's status —
the public list query filters to ``status='approved'`` and surfaces
the media URLs alongside the row.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0035_review_media"
down_revision: str | Sequence[str] | None = "0034_products_seller_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_media",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "review_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="image",
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
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
        sa.CheckConstraint("kind IN ('image')", name="ck_review_media_kind"),
        sa.CheckConstraint(
            "file_size_bytes > 0", name="ck_review_media_size_positive",
        ),
    )
    op.create_index("ix_review_media_review", "review_media", ["review_id"])


def downgrade() -> None:
    op.drop_index("ix_review_media_review", table_name="review_media")
    op.drop_table("review_media")
