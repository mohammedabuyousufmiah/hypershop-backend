"""0051 sprint9_phases — close deferred phases across modules.

Audit revealed product_questions / product_answers / review_media /
sellers.authz are already implemented. This migration only adds the
genuinely missing pieces:

- loyalty_tier_benefits      — per-tier perks (earn multiplier, discount %, free-shipping threshold, birthday bonus)
- loyalty_accounts.tier       — relaxed CHECK constraint to allow new PLATINUM tier
- seller_payouts              — execution history of seller payouts (vs the preview-only flow today)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0051_sprint9_phases"
down_revision: str | Sequence[str] | None = "0050_cc_sprint8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- loyalty_tier_benefits ----
    op.create_table(
        "loyalty_tier_benefits",
        sa.Column("tier", sa.String(16), primary_key=True),
        sa.Column("min_lifetime_points", sa.Integer, nullable=False),
        sa.Column("earn_multiplier", sa.Numeric(3, 2),
                  nullable=False, server_default="1.00"),
        sa.Column("discount_percent", sa.Numeric(5, 2),
                  nullable=False, server_default="0"),
        sa.Column("free_shipping_threshold", sa.Numeric(12, 2), nullable=True),
        sa.Column("birthday_bonus_points", sa.Integer, nullable=False, server_default="0"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )

    op.execute("""
    INSERT INTO loyalty_tier_benefits
        (tier, min_lifetime_points, earn_multiplier, discount_percent,
         free_shipping_threshold, birthday_bonus_points, description)
    VALUES
        ('NONE',     0,    1.00, 0.00,  NULL,  0,   'Base tier'),
        ('BRONZE',   100,  1.10, 2.00,  2000,  50,  'Earn 10% more · 2% off'),
        ('SILVER',   500,  1.25, 5.00,  1500,  100, 'Earn 25% more · 5% off · lower free-ship threshold'),
        ('GOLD',     1500, 1.50, 8.00,  1000,  200, 'Earn 50% more · 8% off · birthday bonus'),
        ('PLATINUM', 5000, 2.00, 12.00, 0,     500, 'Earn 2x · 12% off · free shipping always · top-tier perks')
    """)

    # ---- Relax loyalty tier check to allow PLATINUM ----
    op.execute("""
    DO $$
    DECLARE
        cn text;
    BEGIN
        SELECT conname INTO cn FROM pg_constraint
            WHERE conrelid = 'loyalty_accounts'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%BRONZE%';
        IF cn IS NOT NULL THEN
            EXECUTE format('ALTER TABLE loyalty_accounts DROP CONSTRAINT %I', cn);
        END IF;
        ALTER TABLE loyalty_accounts
            ADD CONSTRAINT ck_loyalty_accounts_tier
            CHECK (tier IN ('NONE','BRONZE','SILVER','GOLD','PLATINUM'));
    END $$;
    """)

    # ---- seller_payouts (executed-payout history) ----
    op.create_table(
        "seller_payouts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("seller_id", UUID(as_uuid=True),
                  sa.ForeignKey("sellers.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gross_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("commission_deducted", sa.Numeric(12, 2), nullable=False),
        sa.Column("return_debit", sa.Numeric(12, 2),
                  nullable=False, server_default="0"),
        sa.Column("net_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8),
                  nullable=False, server_default="BDT"),
        sa.Column("status", sa.String(24),
                  nullable=False, server_default="pending"),
        sa.Column("payment_method", sa.String(24), nullable=True),
        sa.Column("payment_reference", sa.String(80), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','approved','paid','failed','cancelled')",
            name="ck_seller_payouts_status",
        ),
    )
    op.create_index("ix_seller_payouts_seller_period",
                    "seller_payouts", ["seller_id", "period_end"])


def downgrade() -> None:
    op.drop_index("ix_seller_payouts_seller_period", table_name="seller_payouts")
    op.drop_table("seller_payouts")
    op.execute(
        "ALTER TABLE loyalty_accounts DROP CONSTRAINT IF EXISTS ck_loyalty_accounts_tier"
    )
    op.execute(
        "ALTER TABLE loyalty_accounts ADD CONSTRAINT ck_loyalty_accounts_tier "
        "CHECK (tier IN ('NONE','BRONZE','SILVER','GOLD'))"
    )
    op.drop_table("loyalty_tier_benefits")
