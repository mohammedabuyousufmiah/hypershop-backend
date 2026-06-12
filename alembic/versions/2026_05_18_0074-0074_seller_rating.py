"""0074 seller_rating — per-seller quality score + history snapshots."""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0074_seller_rating"
down_revision: str | Sequence[str] | None = "0073_cc_inbox_voice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hypershop_seller_ratings",
        sa.Column(
            "seller_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "overall_score",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("50.00"),
        ),
        sa.Column(
            "tier",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'standard'"),
        ),
        sa.Column("on_time_shipping_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("return_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("dispute_resolution_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("response_time_hours", sa.Numeric(7, 2), nullable=True),
        sa.Column("review_avg", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "review_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "order_count_30d",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
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
            "overall_score BETWEEN 0 AND 100",
            name="ck_hypershop_seller_ratings_score_range",
        ),
        sa.CheckConstraint(
            "tier IN ('platinum','gold','silver','standard','poor','suspended')",
            name="ck_hypershop_seller_ratings_tier",
        ),
    )
    op.create_index(
        "ix_hypershop_seller_ratings_score_desc",
        "hypershop_seller_ratings",
        [sa.text("overall_score DESC")],
    )
    op.create_index(
        "ix_hypershop_seller_ratings_tier",
        "hypershop_seller_ratings",
        ["tier"],
    )

    op.create_table(
        "hypershop_seller_rating_snapshots",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "seller_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_hypershop_seller_rating_snapshots_seller_at",
        "hypershop_seller_rating_snapshots",
        ["seller_id", sa.text("computed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hypershop_seller_rating_snapshots_seller_at",
        table_name="hypershop_seller_rating_snapshots",
    )
    op.drop_table("hypershop_seller_rating_snapshots")
    op.drop_index(
        "ix_hypershop_seller_ratings_tier",
        table_name="hypershop_seller_ratings",
    )
    op.drop_index(
        "ix_hypershop_seller_ratings_score_desc",
        table_name="hypershop_seller_ratings",
    )
    op.drop_table("hypershop_seller_ratings")
