"""Rider wallet + settlement — 5 tables.

Revision ID: 0026_rider_wallet
Revises: 0025_rider_routing
Create Date: 2026-05-04

Module 32. See ``app/modules/rider_wallet/models.py`` for table-by-table
docstrings. Order matters: rider_settlements must precede
rider_wallet_ledger (which has settlement_id FK).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_rider_wallet"
down_revision: str | Sequence[str] | None = "0025_rider_routing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- rider_wallets ----------------
    op.create_table(
        "rider_wallets",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False, unique=True,
        ),
        sa.Column(
            "cash_in_hand", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "wallet_payable_to_company", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "wallet_pending_settlement", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "wallet_credit_adjustment", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "wallet_status", sa.String(32),
            nullable=False, server_default="clear",
        ),
        sa.Column(
            "last_settlement_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "is_frozen", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "assignment_locked", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "assignment_locked_reason", sa.String(255), nullable=True,
        ),
        sa.Column("overdue_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "carry_forward_approved", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "carry_forward_approved_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "carry_forward_amount", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "carry_forward_expires_at", sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "wallet_status IN ('clear','has_cod_balance',"
            "'settlement_submitted','partially_settled',"
            "'settlement_overdue','overdue_blocked','frozen')",
            name="ck_rider_wallets_status_enum",
        ),
        sa.CheckConstraint(
            "cash_in_hand >= 0 AND wallet_payable_to_company >= 0 "
            "AND wallet_pending_settlement >= 0 "
            "AND wallet_credit_adjustment >= 0",
            name="ck_rider_wallets_nonneg",
        ),
    )
    op.create_index(
        "ix_rider_wallets_locked_status",
        "rider_wallets",
        ["assignment_locked", "wallet_status"],
    )

    # ---------------- rider_settlements (must precede ledger.settlement_id) -
    op.create_table(
        "rider_settlements",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "shift_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rider_shifts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("settlement_date", sa.Date, nullable=False),
        sa.Column("mfs_provider", sa.String(16), nullable=False),
        sa.Column("sender_mfs_number", sa.String(32), nullable=False),
        sa.Column("company_receiver_account", sa.String(64), nullable=False),
        sa.Column("submitted_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("verified_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("transaction_id", sa.String(120), nullable=False, unique=True),
        sa.Column("transaction_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("proof_image_url", sa.String(512), nullable=True),
        sa.Column(
            "status", sa.String(16),
            nullable=False, server_default="submitted",
        ),
        sa.Column("review_note", sa.Text, nullable=True),
        sa.Column(
            "reviewed_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "mfs_provider IN ('bkash','nagad','rocket','other')",
            name="ck_rider_settlements_mfs_enum",
        ),
        sa.CheckConstraint(
            "status IN ('draft','submitted','under_review','verified',"
            "'rejected','adjusted')",
            name="ck_rider_settlements_status_enum",
        ),
        sa.CheckConstraint(
            "submitted_amount > 0",
            name="ck_rider_settlements_submitted_pos",
        ),
        sa.CheckConstraint(
            "verified_amount IS NULL OR verified_amount >= 0",
            name="ck_rider_settlements_verified_nonneg",
        ),
    )
    op.create_index(
        "ix_rider_settlements_rider_status_date",
        "rider_settlements",
        ["rider_id", "status", "settlement_date"],
    )

    # ---------------- rider_wallet_ledger (append-only) ----------------
    op.create_table(
        "rider_wallet_ledger",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "shift_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rider_shifts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "delivery_assignment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_assignments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "settlement_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rider_settlements.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entry_type", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(14, 2), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "entry_type IN ('cod_collection','settlement_submitted',"
            "'settlement_verified','settlement_rejected',"
            "'settlement_adjusted','shortage_penalty',"
            "'excess_adjustment','cash_deposit_correction')",
            name="ck_rider_wallet_ledger_entry_type_enum",
        ),
        sa.CheckConstraint(
            "direction IN ('debit','credit')",
            name="ck_rider_wallet_ledger_direction_enum",
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_rider_wallet_ledger_amount_nonneg",
        ),
    )
    op.create_index(
        "ix_rider_wallet_ledger_rider_created",
        "rider_wallet_ledger",
        ["rider_id", "created_at"],
    )
    op.create_index(
        "ix_rider_wallet_ledger_assignment",
        "rider_wallet_ledger",
        ["delivery_assignment_id"],
    )
    # Append-only enforcement at the DB level — same pattern as
    # delivery_status_history / audit_log / stock_ledger.
    op.execute(
        "REVOKE UPDATE, DELETE ON rider_wallet_ledger FROM public",
    )

    # ---------------- rider_cash_limits ----------------
    op.create_table(
        "rider_cash_limits",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column(
            "max_cash_in_hand", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "max_unsettled_amount", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "allow_carry_forward", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "carry_forward_limit", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "max_cash_in_hand >= 0 AND max_unsettled_amount >= 0 "
            "AND carry_forward_limit >= 0",
            name="ck_rider_cash_limits_nonneg",
        ),
    )

    # ---------------- rider_wallet_daily_summaries ----------------
    op.create_table(
        "rider_wallet_daily_summaries",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "shift_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rider_shifts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("summary_date", sa.Date, nullable=False),
        sa.Column(
            "total_cod_collected", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "total_submitted", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "total_verified", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "total_pending", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "total_short", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "total_excess", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "closing_payable", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "clearance_status", sa.String(32),
            nullable=False, server_default="cleared",
        ),
        sa.Column(
            "is_cleared_for_next_shift", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "blocked_amount", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint(
            "rider_id", "summary_date",
            name="uq_rider_wallet_daily_rider_date",
        ),
        sa.CheckConstraint(
            "clearance_status IN ('cleared','pending_settlement',"
            "'pending_verification','partial_settlement',"
            "'overdue_blocked','carry_forward_approved')",
            name="ck_rider_wallet_daily_clearance_enum",
        ),
    )
    op.create_index(
        "ix_rider_wallet_daily_rider_date",
        "rider_wallet_daily_summaries",
        ["rider_id", "summary_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rider_wallet_daily_rider_date",
        table_name="rider_wallet_daily_summaries",
    )
    op.drop_table("rider_wallet_daily_summaries")

    op.drop_table("rider_cash_limits")

    op.execute(
        "GRANT UPDATE, DELETE ON rider_wallet_ledger TO public",
    )
    op.drop_index(
        "ix_rider_wallet_ledger_assignment",
        table_name="rider_wallet_ledger",
    )
    op.drop_index(
        "ix_rider_wallet_ledger_rider_created",
        table_name="rider_wallet_ledger",
    )
    op.drop_table("rider_wallet_ledger")

    op.drop_index(
        "ix_rider_settlements_rider_status_date",
        table_name="rider_settlements",
    )
    op.drop_table("rider_settlements")

    op.drop_index(
        "ix_rider_wallets_locked_status",
        table_name="rider_wallets",
    )
    op.drop_table("rider_wallets")
