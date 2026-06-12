"""0038 loyalty — accounts + transactions ledger.

Phase-1 loyalty (built 2026-05-11 per locked spec):
  * Earn: 1 pt / 100 BDT spent
  * Tiers (lifetime points): BRONZE >=100, SILVER >=200, GOLD >=300
  * Redemption: 1 pt = 0.50 BDT; capped at 3% of order subtotal
  * Expiry: 12 months rolling, per EARN row

Two tables:
  * ``loyalty_accounts`` — one row per customer user. ``balance_points``
    is a denormalised running total kept consistent by the service;
    ``lifetime_earned_points`` is monotonic (never decremented even on
    expiry) so the tier ladder is stable. ``tier_recomputed_at`` lets
    a cron know when to nudge tier transitions.
  * ``loyalty_transactions`` — append-only ledger. EARN rows carry
    ``expires_at``; REDEEM rows carry ``source_order_id``. The signed
    ``points`` column is +ve for EARN / -ve for REDEEM / EXPIRE so a
    SUM gives the lifetime net.

Indexes:
  * (user_id, created_at) for "my transactions newest-first" reads
  * (expires_at) for the daily expiry sweep (only EARN rows have it)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


revision: str = "0038_loyalty"
down_revision: str | Sequence[str] | None = "0037_cart_checkout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loyalty_accounts",
        sa.Column("user_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "balance_points",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "lifetime_earned_points",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "tier",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'NONE'"),
        ),
        sa.Column(
            "tier_recomputed_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
            "balance_points >= 0",
            name="ck_loyalty_balance_nonneg",
        ),
        sa.CheckConstraint(
            "lifetime_earned_points >= 0",
            name="ck_loyalty_lifetime_nonneg",
        ),
        sa.CheckConstraint(
            "tier IN ('NONE','BRONZE','SILVER','GOLD')",
            name="ck_loyalty_tier_valid",
        ),
    )

    op.create_table(
        "loyalty_transactions",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("txn_type", sa.String(16), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("source_order_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "txn_type IN ('EARN','REDEEM','EXPIRE','ADJUST')",
            name="ck_loyalty_txn_type",
        ),
        sa.CheckConstraint(
            "points <> 0",
            name="ck_loyalty_points_nonzero",
        ),
    )
    op.create_index(
        "ix_loyalty_txn_user_created",
        "loyalty_transactions",
        ["user_id", sa.text("created_at DESC")],
    )
    # Partial index — only EARN rows carry expires_at, so the sweep cron
    # has a tight working set.
    op.create_index(
        "ix_loyalty_txn_expires_due",
        "loyalty_transactions",
        ["expires_at"],
        postgresql_where=sa.text(
            "txn_type = 'EARN' AND expires_at IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_loyalty_txn_source_order",
        "loyalty_transactions",
        ["source_order_id"],
        postgresql_where=sa.text("source_order_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_loyalty_txn_source_order", table_name="loyalty_transactions")
    op.drop_index("ix_loyalty_txn_expires_due", table_name="loyalty_transactions")
    op.drop_index("ix_loyalty_txn_user_created", table_name="loyalty_transactions")
    op.drop_table("loyalty_transactions")
    op.drop_table("loyalty_accounts")
