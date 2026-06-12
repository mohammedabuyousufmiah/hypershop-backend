"""reviews module — verified-purchase 1-5 star reviews + helpful votes + aggregate

Revision ID: 0032_reviews
Revises: 0031_product_videos_timeline_fields
Create Date: 2026-05-10

Phase 1 of the reviews initiative (see ``docs/SCOPING_REVIEWS.md``).
Three tables:

- ``product_reviews`` — one row per (customer, product), unique
  constraint enforces phase-1 "one review per customer" rule
- ``review_helpful_votes`` — composite-PK upvote, idempotent
- ``product_review_aggregates`` — denormalised avg_rating +
  review_count per product, recomputed by service code
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0032_reviews"
down_revision: str | Sequence[str] | None = "0031_product_videos_timeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUSES = ("pending", "approved", "rejected", "disabled")
_STATUS_CHECK = "status IN (" + ",".join(f"'{s}'" for s in _STATUSES) + ")"


def upgrade() -> None:
    op.create_table(
        "product_reviews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "helpful_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("rejection_reason", sa.String(length=300), nullable=True),
        sa.Column(
            "moderated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
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
            "rating BETWEEN 1 AND 5", name="ck_review_rating_range",
        ),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_review_status"),
        sa.UniqueConstraint(
            "customer_id", "product_id", name="uq_review_customer_product",
        ),
    )
    op.create_index(
        "ix_reviews_product_status", "product_reviews",
        ["product_id", "status"],
    )
    op.create_index(
        "ix_reviews_customer", "product_reviews", ["customer_id"],
    )
    op.create_index(
        "ix_reviews_product_helpful", "product_reviews",
        ["product_id", "helpful_count", "created_at"],
    )

    op.create_table(
        "review_helpful_votes",
        sa.Column(
            "review_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_reviews.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "voted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_helpful_votes_review", "review_helpful_votes", ["review_id"],
    )

    op.create_table(
        "product_review_aggregates",
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "avg_rating",
            sa.Numeric(3, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "review_count",
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
        sa.CheckConstraint(
            "avg_rating BETWEEN 0 AND 5",
            name="ck_aggregate_avg_rating_range",
        ),
        sa.CheckConstraint(
            "review_count >= 0", name="ck_aggregate_review_count_nonneg",
        ),
    )


def downgrade() -> None:
    op.drop_table("product_review_aggregates")
    op.drop_index("ix_helpful_votes_review", table_name="review_helpful_votes")
    op.drop_table("review_helpful_votes")
    op.drop_index("ix_reviews_product_helpful", table_name="product_reviews")
    op.drop_index("ix_reviews_customer", table_name="product_reviews")
    op.drop_index("ix_reviews_product_status", table_name="product_reviews")
    op.drop_table("product_reviews")
