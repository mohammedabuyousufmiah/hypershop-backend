"""0075 customer_segments — RFM cohort analysis + named audience segments.

Three tables:
- hypershop_customer_rfm_scores         — per-customer recency/frequency/monetary
- hypershop_customer_segments           — admin-editable segment definitions
- hypershop_customer_segment_memberships — denormalized assignment lookup

Distinct from the existing funnel_segments table (behavior-cohort definitions).

Revision ID: 0075_customer_segments
Revises:    0074_seller_rating
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0075_customer_segments"
down_revision = "0074_seller_rating"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hypershop_customer_rfm_scores",
        sa.Column(
            "customer_user_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("recency_days", sa.Integer, nullable=False),
        sa.Column("frequency_count", sa.Integer, nullable=False),
        sa.Column("monetary_minor", sa.BigInteger, nullable=False),
        sa.Column("recency_score", sa.SmallInteger, nullable=False),
        sa.Column("frequency_score", sa.SmallInteger, nullable=False),
        sa.Column("monetary_score", sa.SmallInteger, nullable=False),
        sa.Column("rfm_total", sa.SmallInteger, nullable=False),
        sa.Column("segment_code", sa.String(32), nullable=False),
        sa.Column("last_order_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_user_id"], ["users.id"], ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "recency_score BETWEEN 1 AND 5",
            name="ck_rfm_recency_score_range",
        ),
        sa.CheckConstraint(
            "frequency_score BETWEEN 1 AND 5",
            name="ck_rfm_frequency_score_range",
        ),
        sa.CheckConstraint(
            "monetary_score BETWEEN 1 AND 5",
            name="ck_rfm_monetary_score_range",
        ),
        sa.CheckConstraint(
            "rfm_total BETWEEN 3 AND 15",
            name="ck_rfm_total_range",
        ),
    )
    op.create_index(
        "ix_rfm_scores_segment_computed",
        "hypershop_customer_rfm_scores",
        ["segment_code", sa.text("computed_at DESC")],
    )
    op.create_index(
        "ix_rfm_scores_total_desc",
        "hypershop_customer_rfm_scores",
        [sa.text("rfm_total DESC")],
    )

    op.create_table(
        "hypershop_customer_segments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name_en", sa.String(120), nullable=False),
        sa.Column("name_bn", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "rule",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("estimated_size", sa.Integer, nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
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
    )
    op.create_index(
        "ix_customer_segments_active_code",
        "hypershop_customer_segments",
        ["is_active", "code"],
    )

    op.create_table(
        "hypershop_customer_segment_memberships",
        sa.Column(
            "segment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "customer_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.PrimaryKeyConstraint(
            "segment_id", "customer_user_id",
            name="pk_customer_segment_memberships",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["hypershop_customer_segments.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_segment_memberships_customer_added",
        "hypershop_customer_segment_memberships",
        ["customer_user_id", sa.text("added_at DESC")],
    )

    # Seed 6 default RFM-based segments. Each rule is a JSON object the
    # service layer interprets when materializing memberships.
    seeds = [
        (
            "rfm_vip",
            "VIP customers",
            "ভিআইপি গ্রাহক",
            "Recency >= 4 AND Frequency >= 4 AND Monetary >= 4",
            '{"type":"rfm","segments":["vip"]}',
        ),
        (
            "rfm_loyal",
            "Loyal customers",
            "অনুগত গ্রাহক",
            "Frequency >= 4",
            '{"type":"rfm","segments":["loyal","vip"]}',
        ),
        (
            "rfm_at_risk",
            "At-risk customers",
            "ঝুঁকিতে গ্রাহক",
            "Recency <= 2 AND Frequency >= 3",
            '{"type":"rfm","segments":["at_risk"]}',
        ),
        (
            "rfm_new",
            "New customers",
            "নতুন গ্রাহক",
            "Recency >= 4 AND Frequency <= 2",
            '{"type":"rfm","segments":["new"]}',
        ),
        (
            "rfm_dormant",
            "Dormant customers",
            "নিষ্ক্রিয় গ্রাহক",
            "Recency <= 2 AND Frequency <= 2",
            '{"type":"rfm","segments":["dormant"]}',
        ),
        (
            "rfm_cant_lose",
            "Can't lose them",
            "হারানো যাবে না",
            "Recency <= 2 AND Monetary >= 4",
            '{"type":"rfm","segments":["cant_lose"]}',
        ),
    ]
    for code, name_en, name_bn, desc, rule_json in seeds:
        op.execute(
            sa.text(
                """
                INSERT INTO hypershop_customer_segments
                  (code, name_en, name_bn, description, rule, is_active)
                VALUES
                  (:code, :name_en, :name_bn, :desc, CAST(:rule AS JSONB), true)
                ON CONFLICT (code) DO NOTHING
                """
            ).bindparams(
                code=code,
                name_en=name_en,
                name_bn=name_bn,
                desc=desc,
                rule=rule_json,
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_segment_memberships_customer_added",
        table_name="hypershop_customer_segment_memberships",
    )
    op.drop_table("hypershop_customer_segment_memberships")
    op.drop_index(
        "ix_customer_segments_active_code",
        table_name="hypershop_customer_segments",
    )
    op.drop_table("hypershop_customer_segments")
    op.drop_index(
        "ix_rfm_scores_total_desc",
        table_name="hypershop_customer_rfm_scores",
    )
    op.drop_index(
        "ix_rfm_scores_segment_computed",
        table_name="hypershop_customer_rfm_scores",
    )
    op.drop_table("hypershop_customer_rfm_scores")
