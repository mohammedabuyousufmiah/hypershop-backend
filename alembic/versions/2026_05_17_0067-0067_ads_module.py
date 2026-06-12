"""0067 ads_module — Sponsored Products Phase 1.A schema.

Seven tables (all prefixed ``hypershop_ad_``):
  hypershop_ad_wallets             — per-seller ad balance + auto-recharge
  hypershop_ad_wallet_txns         — signed append-only ad-wallet ledger
  hypershop_ad_campaigns           — seller-owned spend buckets
  hypershop_ad_groups              — bid + targeting + product set
  hypershop_ad_impressions         — high-volume slot-render log
  hypershop_ad_clicks              — billable clicks (1 per impression)
  hypershop_ad_quality_snapshots   — periodic quality-score recomputes

Money in BIGINT minor (paisa). Impressions + clicks use BIGINT identity
(too high-volume for gen_random_uuid). Indices match the Phase 1.D read
patterns (per-surface auction lookup, per-seller spend rollup, GIN on
targets for keyword/category match).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

# revision identifiers, used by Alembic.
revision: str = "0067_ads_module"
down_revision: str | Sequence[str] | None = "0066_drop_prescription_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── hypershop_ad_wallets ─────────────────────────────────────
    op.create_table(
        "hypershop_ad_wallets",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "seller_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "balance_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_spent_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "auto_recharge_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("auto_recharge_threshold_minor", sa.BigInteger(), nullable=True),
        sa.Column("auto_recharge_amount_minor", sa.BigInteger(), nullable=True),
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
        sa.UniqueConstraint("seller_id", name="uq_hypershop_ad_wallets_seller"),
        sa.CheckConstraint(
            "balance_minor >= 0",
            name="ck_hypershop_ad_wallets_balance_nonneg",
        ),
    )

    # ─── hypershop_ad_wallet_txns ─────────────────────────────────
    op.create_table(
        "hypershop_ad_wallet_txns",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "ad_wallet_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_ad_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        # Signed amount; positive = credit, negative = debit.
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_minor", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=True),
        sa.Column("source_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("memo", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "balance_after_minor >= 0",
            name="ck_hypershop_ad_wallet_txns_balance_nonneg",
        ),
        sa.CheckConstraint(
            "kind IN ('recharge','click_debit','refund','manual_credit','adjust')",
            name="ck_hypershop_ad_wallet_txns_kind",
        ),
    )
    op.create_index(
        "ix_hypershop_ad_wallet_txns_wallet_at",
        "hypershop_ad_wallet_txns",
        ["ad_wallet_id", sa.text("created_at DESC")],
    )

    # ─── hypershop_ad_campaigns ───────────────────────────────────
    op.create_table(
        "hypershop_ad_campaigns",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "seller_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("daily_budget_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_budget_minor", sa.BigInteger(), nullable=True),
        sa.Column(
            "total_spent_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "today_spent_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
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
            "status IN ('draft','active','paused','ended','budget_exhausted')",
            name="ck_hypershop_ad_campaigns_status",
        ),
        sa.CheckConstraint(
            "daily_budget_minor >= 0",
            name="ck_hypershop_ad_campaigns_daily_budget_nonneg",
        ),
    )
    op.create_index(
        "ix_hypershop_ad_campaigns_seller_status",
        "hypershop_ad_campaigns",
        ["seller_id", "status"],
    )
    op.create_index(
        "ix_hypershop_ad_campaigns_active_end",
        "hypershop_ad_campaigns",
        ["status", "end_at"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # ─── hypershop_ad_groups ──────────────────────────────────────
    op.create_table(
        "hypershop_ad_groups",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "campaign_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_ad_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("default_bid_minor", sa.BigInteger(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("targets", JSONB, nullable=False),
        sa.Column(
            "product_ids",
            ARRAY(PgUUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column(
            "quality_score",
            sa.Numeric(3, 2),
            nullable=False,
            server_default=sa.text("1.00"),
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
            "status IN ('active','paused')",
            name="ck_hypershop_ad_groups_status",
        ),
        sa.CheckConstraint(
            "default_bid_minor >= 200",
            name="ck_hypershop_ad_groups_min_bid",
        ),
        sa.CheckConstraint(
            "target_type IN ('keyword','category','auto')",
            name="ck_hypershop_ad_groups_target_type",
        ),
        sa.CheckConstraint(
            "quality_score >= 0.50 AND quality_score <= 1.50",
            name="ck_hypershop_ad_groups_quality_range",
        ),
    )
    op.create_index(
        "ix_hypershop_ad_groups_targets_gin",
        "hypershop_ad_groups",
        ["targets"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_hypershop_ad_groups_campaign_status",
        "hypershop_ad_groups",
        ["campaign_id", "status"],
    )

    # ─── hypershop_ad_impressions ─────────────────────────────────
    op.create_table(
        "hypershop_ad_impressions",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "ad_group_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_ad_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("surface", sa.String(16), nullable=False),
        sa.Column("surface_ref", sa.String(256), nullable=False),
        sa.Column("slot_position", sa.SmallInteger(), nullable=False),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("bid_at_auction_minor", sa.BigInteger(), nullable=False),
        sa.Column("cleared_price_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "surface IN ('search','category','pdp_related')",
            name="ck_hypershop_ad_impressions_surface",
        ),
    )
    op.create_index(
        "ix_hypershop_ad_impressions_group_at",
        "hypershop_ad_impressions",
        ["ad_group_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_hypershop_ad_impressions_surface_ref_at",
        "hypershop_ad_impressions",
        ["surface", "surface_ref", sa.text("created_at DESC")],
    )

    # ─── hypershop_ad_clicks ──────────────────────────────────────
    op.create_table(
        "hypershop_ad_clicks",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "impression_id",
            sa.BigInteger(),
            sa.ForeignKey("hypershop_ad_impressions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ad_group_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_ad_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("seller_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("charged_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "wallet_txn_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_ad_wallet_txns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "is_invalid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("invalid_reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_hypershop_ad_clicks_seller_at",
        "hypershop_ad_clicks",
        ["seller_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_hypershop_ad_clicks_impression_valid",
        "hypershop_ad_clicks",
        ["impression_id"],
        unique=True,
        postgresql_where=sa.text("is_invalid = false"),
    )

    # ─── hypershop_ad_quality_snapshots ───────────────────────────
    op.create_table(
        "hypershop_ad_quality_snapshots",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "ad_group_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_ad_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ctr", sa.Numeric(5, 4), nullable=True),
        sa.Column("rating_avg", sa.Numeric(3, 2), nullable=True),
        sa.Column("in_stock_rate", sa.Numeric(3, 2), nullable=True),
        sa.Column("computed_score", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_hypershop_ad_quality_snapshots_group_at",
        "hypershop_ad_quality_snapshots",
        ["ad_group_id", sa.text("computed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hypershop_ad_quality_snapshots_group_at",
        table_name="hypershop_ad_quality_snapshots",
    )
    op.drop_table("hypershop_ad_quality_snapshots")

    op.drop_index(
        "uq_hypershop_ad_clicks_impression_valid",
        table_name="hypershop_ad_clicks",
    )
    op.drop_index(
        "ix_hypershop_ad_clicks_seller_at",
        table_name="hypershop_ad_clicks",
    )
    op.drop_table("hypershop_ad_clicks")

    op.drop_index(
        "ix_hypershop_ad_impressions_surface_ref_at",
        table_name="hypershop_ad_impressions",
    )
    op.drop_index(
        "ix_hypershop_ad_impressions_group_at",
        table_name="hypershop_ad_impressions",
    )
    op.drop_table("hypershop_ad_impressions")

    op.drop_index(
        "ix_hypershop_ad_groups_campaign_status",
        table_name="hypershop_ad_groups",
    )
    op.drop_index(
        "ix_hypershop_ad_groups_targets_gin",
        table_name="hypershop_ad_groups",
    )
    op.drop_table("hypershop_ad_groups")

    op.drop_index(
        "ix_hypershop_ad_campaigns_active_end",
        table_name="hypershop_ad_campaigns",
    )
    op.drop_index(
        "ix_hypershop_ad_campaigns_seller_status",
        table_name="hypershop_ad_campaigns",
    )
    op.drop_table("hypershop_ad_campaigns")

    op.drop_index(
        "ix_hypershop_ad_wallet_txns_wallet_at",
        table_name="hypershop_ad_wallet_txns",
    )
    op.drop_table("hypershop_ad_wallet_txns")

    op.drop_table("hypershop_ad_wallets")
