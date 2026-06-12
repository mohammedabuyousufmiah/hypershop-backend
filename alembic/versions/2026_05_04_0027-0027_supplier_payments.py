"""Supplier payment approval engine — 5 tables + ALTER fin_supplier_payments.

Revision ID: 0027_supplier_payments
Revises: 0026_rider_wallet
Create Date: 2026-05-04

Module 33. See ``app/modules/supplier_payments/models.py`` for
table-by-table docstrings.

ALTER fin_supplier_payments to add:
  - verification_status   (default 'pending')
  - proof_file_url        (nullable)
  - executed_by, verified_by  (FK users)
  - bank_account_id       (FK supplier_bank_accounts; nullable for cash)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_supplier_payments"
down_revision: str | Sequence[str] | None = "0026_rider_wallet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- supplier_bank_accounts (must precede ALTER) ----
    op.create_table(
        "supplier_bank_accounts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "supplier_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_type", sa.String(8), nullable=False),
        sa.Column("account_name", sa.String(160), nullable=False),
        sa.Column("bank_name", sa.String(120), nullable=True),
        sa.Column("account_number_masked", sa.String(64), nullable=True),
        sa.Column("iban_or_branch", sa.String(255), nullable=True),
        sa.Column("mfs_number", sa.String(32), nullable=True),
        sa.Column(
            "is_default", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "is_verified", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "verified_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "is_active", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
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
            "account_type IN ('bank','mfs','cash')",
            name="ck_sba_acct_type_enum",
        ),
    )
    op.create_index(
        "uq_sba_supplier_default",
        "supplier_bank_accounts", ["supplier_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND is_active = true"),
    )
    op.create_index(
        "ix_sba_supplier_active",
        "supplier_bank_accounts", ["supplier_id", "is_active"],
    )

    # ---------------- supplier_bill_approval_state ----------------
    op.create_table(
        "supplier_bill_approval_state",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "supplier_bill_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_supplier_bills.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column(
            "approval_status", sa.String(32),
            nullable=False, server_default="draft",
        ),
        sa.Column("workflow_code", sa.String(80), nullable=True),
        sa.Column("recommended_payment_date", sa.Date, nullable=True),
        sa.Column("recommended_payment_method", sa.String(32), nullable=True),
        sa.Column("recommended_payment_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("recommendation_score", sa.Integer, nullable=True),
        sa.Column(
            "payment_priority", sa.String(16),
            nullable=False, server_default="normal",
        ),
        sa.Column(
            "last_recommended_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "dispute_flag", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("dispute_reason", sa.Text, nullable=True),
        sa.Column(
            "duplicate_check_flag", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "duplicate_of_bill_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_supplier_bills.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "selected_bank_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_bank_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "submitted_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("final_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "approval_status IN ('draft','submitted','level_1_verified',"
            "'level_2_approved','level_3_approved','super_admin_required',"
            "'approved_final','ready_for_payment','paid','reconciled',"
            "'returned_for_correction','rejected','on_hold')",
            name="ck_sbas_status_enum",
        ),
        sa.CheckConstraint(
            "payment_priority IN ('critical','high','normal','low','on_hold')",
            name="ck_sbas_priority_enum",
        ),
        sa.CheckConstraint(
            "recommendation_score IS NULL OR "
            "(recommendation_score >= 0 AND recommendation_score <= 100)",
            name="ck_sbas_score_range",
        ),
    )
    op.create_index(
        "ix_sbas_status_priority",
        "supplier_bill_approval_state",
        ["approval_status", "payment_priority"],
    )
    op.create_index(
        "ix_sbas_dispute",
        "supplier_bill_approval_state", ["dispute_flag"],
    )
    op.create_index(
        "ix_sbas_duplicate",
        "supplier_bill_approval_state", ["duplicate_check_flag"],
    )

    # ---------------- supplier_bill_approvals (append-only) ----------
    op.create_table(
        "supplier_bill_approvals",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "supplier_bill_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_supplier_bills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column(
            "approver_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("approver_role", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("decision_note", sa.Text, nullable=True),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint(
            "supplier_bill_id", "level",
            name="uq_sba_bill_level",
        ),
        sa.CheckConstraint(
            "level >= 1 AND level <= 4",
            name="ck_sba_level_bounds",
        ),
        sa.CheckConstraint(
            "decision IN ('pending','approved','rejected',"
            "'returned_for_correction')",
            name="ck_sba_decision_enum",
        ),
    )
    op.create_index(
        "ix_sba_bill_level",
        "supplier_bill_approvals",
        ["supplier_bill_id", "level"],
    )
    # Append-only enforcement.
    op.execute(
        "REVOKE UPDATE, DELETE ON supplier_bill_approvals FROM public",
    )

    # ---------------- supplier_payment_recommendations ----------------
    op.create_table(
        "supplier_payment_recommendations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "supplier_bill_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_supplier_bills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recommended_payment_date", sa.Date, nullable=False),
        sa.Column("recommended_payment_amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("recommended_payment_method", sa.String(32), nullable=True),
        sa.Column("priority_level", sa.String(16), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column(
            "payload_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "engine_version", sa.String(16),
            nullable=False, server_default="v1",
        ),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "priority_level IN ('critical','high','normal','low','on_hold')",
            name="ck_spr_priority_enum",
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_spr_score_range",
        ),
    )
    op.create_index(
        "ix_spr_bill_generated",
        "supplier_payment_recommendations",
        ["supplier_bill_id", "generated_at"],
    )

    # ---------------- approval_workflows ----------------
    op.create_table(
        "supplier_payment_approval_workflows",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workflow_code", sa.String(80), nullable=False, unique=True,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("threshold_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column(
            "requires_super_admin", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "min_approval_steps", sa.Integer,
            nullable=False, server_default=sa.text("3"),
        ),
        sa.Column(
            "is_active", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
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
            "min_approval_steps >= 1 AND min_approval_steps <= 4",
            name="ck_apw_steps_range",
        ),
        sa.CheckConstraint(
            "threshold_amount IS NULL OR threshold_amount >= 0",
            name="ck_apw_threshold_nonneg",
        ),
    )

    # ---------------- ALTER fin_supplier_payments ----------------
    op.add_column(
        "fin_supplier_payments",
        sa.Column(
            "verification_status", sa.String(24),
            nullable=False, server_default="pending",
        ),
    )
    op.add_column(
        "fin_supplier_payments",
        sa.Column("proof_file_url", sa.String(512), nullable=True),
    )
    op.add_column(
        "fin_supplier_payments",
        sa.Column(
            "executed_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "fin_supplier_payments",
        sa.Column(
            "verified_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "fin_supplier_payments",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "fin_supplier_payments",
        sa.Column(
            "bank_account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_bank_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_fsp_verification_status_enum",
        "fin_supplier_payments",
        "verification_status IN ('pending','proof_uploaded','verified',"
        "'reconciled','disputed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_fsp_verification_status_enum",
        "fin_supplier_payments",
        type_="check",
    )
    for col in (
        "bank_account_id", "verified_at", "verified_by",
        "executed_by", "proof_file_url", "verification_status",
    ):
        op.drop_column("fin_supplier_payments", col)

    op.drop_table("supplier_payment_approval_workflows")

    op.drop_index(
        "ix_spr_bill_generated",
        table_name="supplier_payment_recommendations",
    )
    op.drop_table("supplier_payment_recommendations")

    op.execute(
        "GRANT UPDATE, DELETE ON supplier_bill_approvals TO public",
    )
    op.drop_index(
        "ix_sba_bill_level", table_name="supplier_bill_approvals",
    )
    op.drop_table("supplier_bill_approvals")

    op.drop_index(
        "ix_sbas_duplicate", table_name="supplier_bill_approval_state",
    )
    op.drop_index(
        "ix_sbas_dispute", table_name="supplier_bill_approval_state",
    )
    op.drop_index(
        "ix_sbas_status_priority",
        table_name="supplier_bill_approval_state",
    )
    op.drop_table("supplier_bill_approval_state")

    op.drop_index(
        "ix_sba_supplier_active",
        table_name="supplier_bank_accounts",
    )
    op.drop_index(
        "uq_sba_supplier_default",
        table_name="supplier_bank_accounts",
    )
    op.drop_table("supplier_bank_accounts")
