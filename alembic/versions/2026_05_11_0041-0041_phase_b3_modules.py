"""0041 phase B-3 — analytics_events, feature_flags, customer_notifications,
coupons + coupon_redemptions, tax_rules, fraud_assessments, support_tickets.

Bundled into one migration because each table is small and they all
land together. Down-migration is a clean drop of all of them.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


revision: str = "0041_phase_b3"
down_revision: str | Sequence[str] | None = "0040_affiliates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------- analytics ----------
    op.create_table(
        "analytics_events",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("anonymous_id", sa.String(64), nullable=False),
        sa.Column("customer_user_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "properties",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_analytics_events_user_created",
        "analytics_events",
        ["customer_user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("customer_user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_analytics_events_type_created",
        "analytics_events",
        ["event_type", sa.text("created_at DESC")],
    )

    # ---------- feature flags ----------
    op.create_table(
        "feature_flags",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "rollout_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("100"),
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
            "rollout_percent BETWEEN 0 AND 100",
            name="ck_feature_flag_rollout_range",
        ),
    )

    # ---------- customer notifications ----------
    op.create_table(
        "customer_notifications",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("customer_user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "category",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'general'"),
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("action_url", sa.String(500), nullable=True),
        sa.Column(
            "is_read",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_customer_notif_user_created",
        "customer_notifications",
        ["customer_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_customer_notif_unread",
        "customer_notifications",
        ["customer_user_id"],
        postgresql_where=sa.text("is_read = false"),
    )

    # ---------- coupons + redemptions ----------
    op.create_table(
        "coupons",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("description", sa.String(255), nullable=True),
        # PERCENT | FIXED
        sa.Column("discount_type", sa.String(16), nullable=False),
        sa.Column("discount_value_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "min_subtotal_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "max_discount_minor",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column(
            "max_total_uses",
            sa.Integer(),
            nullable=True,  # null = unlimited
        ),
        sa.Column(
            "max_uses_per_customer",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "total_uses",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "discount_type IN ('PERCENT','FIXED')",
            name="ck_coupon_discount_type",
        ),
        sa.CheckConstraint(
            "discount_value_minor > 0",
            name="ck_coupon_discount_positive",
        ),
    )
    op.create_index(
        "ix_coupons_active_code",
        "coupons",
        [sa.text("lower(code)")],
        unique=True,
    )

    op.create_table(
        "coupon_redemptions",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "coupon_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("coupons.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("customer_user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("discount_applied_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "redeemed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "order_id", "coupon_id", name="uq_coupon_redemption_order_coupon"
        ),
    )
    op.create_index(
        "ix_coupon_redemptions_customer",
        "coupon_redemptions",
        ["coupon_id", "customer_user_id"],
    )

    # ---------- tax rules ----------
    op.create_table(
        "tax_rules",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "country",
            sa.String(2),
            nullable=False,
            server_default=sa.text("'BD'"),
        ),
        # category-scoped rule. null = all categories.
        sa.Column("category_slug", sa.String(120), nullable=True),
        sa.Column("rate_bps", sa.Integer(), nullable=False),  # basis points (100 bps = 1%)
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "rate_bps >= 0 AND rate_bps <= 10000",
            name="ck_tax_rule_rate_range",
        ),
    )

    # ---------- fraud assessments ----------
    op.create_table(
        "fraud_assessments",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        # CLEAR | CHALLENGE | BLOCK
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column(
            "signals",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("decided_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_reason", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "decision IN ('CLEAR','CHALLENGE','BLOCK')",
            name="ck_fraud_decision",
        ),
        sa.CheckConstraint(
            "risk_score BETWEEN 0 AND 100",
            name="ck_fraud_risk_score_range",
        ),
    )
    op.create_index(
        "ix_fraud_assessments_subject",
        "fraud_assessments",
        ["subject_type", "subject_id"],
    )

    # ---------- support tickets ----------
    op.create_table(
        "support_tickets",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("customer_user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.String(5000), nullable=False),
        sa.Column(
            "category",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'general'"),
        ),
        sa.Column(
            "priority",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("assigned_to", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", pg.UUID(as_uuid=True), nullable=True),
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
            "status IN ('open','in_progress','waiting_customer','resolved','closed')",
            name="ck_support_ticket_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low','normal','high','urgent')",
            name="ck_support_ticket_priority",
        ),
    )
    op.create_index(
        "ix_support_tickets_customer",
        "support_tickets",
        ["customer_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_support_tickets_status",
        "support_tickets",
        ["status", sa.text("created_at DESC")],
    )

    op.create_table(
        "support_ticket_messages",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "ticket_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("support_tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_user_id", pg.UUID(as_uuid=True), nullable=False),
        # customer | agent
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("body", sa.String(5000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "author_kind IN ('customer','agent')",
            name="ck_support_msg_author_kind",
        ),
    )
    op.create_index(
        "ix_support_msg_ticket",
        "support_ticket_messages",
        ["ticket_id", "created_at"],
    )

    # ---------- settlement runs (period closure) ----------
    op.create_table(
        "settlement_runs",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        # open | closed
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "total_gmv_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_commission_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "period_start", "period_end", name="uq_settlement_run_period"
        ),
        sa.CheckConstraint(
            "status IN ('open','closed')",
            name="ck_settlement_run_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("settlement_runs")
    op.drop_index("ix_support_msg_ticket", table_name="support_ticket_messages")
    op.drop_table("support_ticket_messages")
    op.drop_index("ix_support_tickets_status", table_name="support_tickets")
    op.drop_index("ix_support_tickets_customer", table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_index("ix_fraud_assessments_subject", table_name="fraud_assessments")
    op.drop_table("fraud_assessments")
    op.drop_table("tax_rules")
    op.drop_index(
        "ix_coupon_redemptions_customer", table_name="coupon_redemptions"
    )
    op.drop_table("coupon_redemptions")
    op.drop_index("ix_coupons_active_code", table_name="coupons")
    op.drop_table("coupons")
    op.drop_index("ix_customer_notif_unread", table_name="customer_notifications")
    op.drop_index("ix_customer_notif_user_created", table_name="customer_notifications")
    op.drop_table("customer_notifications")
    op.drop_table("feature_flags")
    op.drop_index(
        "ix_analytics_events_type_created", table_name="analytics_events"
    )
    op.drop_index(
        "ix_analytics_events_user_created", table_name="analytics_events"
    )
    op.drop_table("analytics_events")
