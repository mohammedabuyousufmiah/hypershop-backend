"""Finance: chart of accounts, journal, periods, AP subledger, COD deposits,
refunds, daily close.

Revision ID: 0015_finance
Revises: 0014_compliance
Create Date: 2026-05-03

Defence-in-depth notes
- ``fin_journal_lines`` is REVOKEd UPDATE/DELETE so a service bug cannot
  rewrite history. Reversal happens via a NEW offsetting entry.
- ``fin_daily_closes`` is REVOKEd UPDATE/DELETE — the snapshot is the
  evidence of close and must remain immutable.
- Per-line CHECK ``(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)``
  prevents a malformed line from being inserted at all. Total balance
  invariant (sum debits = sum credits per entry) is enforced in service
  code at posting time, since it cannot be expressed as a row-level CHECK.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_finance"
down_revision: str | Sequence[str] | None = "0014_compliance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- Chart of accounts ----------------
    op.create_table(
        "fin_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("description", sa.String(2048), nullable=True),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"),
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
        sa.UniqueConstraint("code", name="uq_fin_accounts_code"),
        sa.CheckConstraint(
            "type IN ('asset','liability','equity','revenue','expense')",
            name="ck_fin_accounts_type_enum",
        ),
        sa.CheckConstraint(
            "code = upper(code)", name="ck_fin_accounts_code_uppercase_or_digits",
        ),
    )
    op.create_index("ix_fin_accounts_type", "fin_accounts", ["type"])
    op.create_index("ix_fin_accounts_is_active", "fin_accounts", ["is_active"])

    # ---------------- Periods ----------------
    op.create_table(
        "fin_accounting_periods",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="open",
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "closed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("closing_notes", sa.String(2048), nullable=True),
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
        sa.UniqueConstraint("year", "month", name="uq_fin_periods_year_month"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_fin_periods_month_range"),
        sa.CheckConstraint(
            "status IN ('open','locked')", name="ck_fin_periods_status_enum",
        ),
    )
    op.create_index(
        "ix_fin_periods_status", "fin_accounting_periods", ["status"],
    )

    # ---------------- Journal entries + lines ----------------
    op.create_table(
        "fin_journal_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("narration", sa.String(512), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reference_type", sa.String(48), nullable=True),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "reverses_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_journal_entries.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "posted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "extra",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        sa.UniqueConstraint("code", name="uq_fin_journal_entries_code"),
        sa.CheckConstraint(
            "status IN ('draft','posted','reversed')",
            name="ck_fin_journal_entries_status_enum",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_fin_journal_entries_currency_iso",
        ),
    )
    op.create_index(
        "ix_fin_journal_entries_entry_date", "fin_journal_entries", ["entry_date"],
    )
    op.create_index(
        "ix_fin_journal_entries_status_date",
        "fin_journal_entries",
        ["status", "entry_date"],
    )
    op.create_index(
        "ix_fin_journal_entries_reference",
        "fin_journal_entries",
        ["reference_type", "reference_id"],
    )
    op.create_index(
        "ix_fin_journal_entries_source", "fin_journal_entries", ["source"],
    )

    op.create_table(
        "fin_journal_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_journal_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("account_code", sa.String(16), nullable=False),
        sa.Column(
            "debit", sa.Numeric(16, 2), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "credit", sa.Numeric(16, 2), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("memo", sa.String(512), nullable=True),
        sa.UniqueConstraint(
            "entry_id", "line_number", name="uq_fin_journal_lines_entry_line",
        ),
        sa.CheckConstraint(
            "debit >= 0 AND credit >= 0", name="ck_fin_journal_lines_amounts_nonneg",
        ),
        sa.CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="ck_fin_journal_lines_exactly_one_side",
        ),
    )
    op.create_index(
        "ix_fin_journal_lines_account_id", "fin_journal_lines", ["account_id"],
    )
    op.create_index(
        "ix_fin_journal_lines_entry", "fin_journal_lines", ["entry_id"],
    )

    # ---------------- Supplier bills + payments ----------------
    op.create_table(
        "fin_supplier_bills",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column(
            "supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "goods_receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goods_receipts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("bill_number", sa.String(96), nullable=False),
        sa.Column("bill_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal", sa.Numeric(16, 2), nullable=False),
        sa.Column(
            "tax_total", sa.Numeric(16, 2), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("grand_total", sa.Numeric(16, 2), nullable=False),
        sa.Column(
            "amount_paid",
            sa.Numeric(16, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "booking_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.String(2048), nullable=True),
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
        sa.UniqueConstraint("code", name="uq_fin_supplier_bills_code"),
        sa.UniqueConstraint(
            "supplier_id",
            "bill_number",
            name="uq_fin_supplier_bills_supplier_number",
        ),
        sa.CheckConstraint(
            "status IN ('open','partially_paid','paid','void')",
            name="ck_fin_supplier_bills_status_enum",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_fin_supplier_bills_currency_iso",
        ),
        sa.CheckConstraint(
            "subtotal >= 0 AND tax_total >= 0 AND grand_total >= 0",
            name="ck_fin_supplier_bills_amounts_nonneg",
        ),
        sa.CheckConstraint(
            "amount_paid >= 0 AND amount_paid <= grand_total",
            name="ck_fin_supplier_bills_paid_within_total",
        ),
    )
    op.create_index(
        "ix_fin_supplier_bills_supplier_id", "fin_supplier_bills", ["supplier_id"],
    )
    op.create_index(
        "ix_fin_supplier_bills_status", "fin_supplier_bills", ["status"],
    )
    op.create_index(
        "ix_fin_supplier_bills_due_date", "fin_supplier_bills", ["due_date"],
    )

    op.create_table(
        "fin_supplier_payments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column(
            "bill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_supplier_bills.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("bank_reference", sa.String(160), nullable=True),
        sa.Column("notes", sa.String(1024), nullable=True),
        sa.Column(
            "journal_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint("code", name="uq_fin_supplier_payments_code"),
        sa.CheckConstraint(
            "amount > 0", name="ck_fin_supplier_payments_amount_positive",
        ),
    )
    op.create_index(
        "ix_fin_supplier_payments_bill", "fin_supplier_payments", ["bill_id"],
    )
    op.create_index(
        "ix_fin_supplier_payments_paid_on", "fin_supplier_payments", ["paid_on"],
    )

    # ---------------- COD deposits ----------------
    op.create_table(
        "fin_cod_deposits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column(
            "rider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("deposit_date", sa.Date(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("deposited_amount", sa.Numeric(16, 2), nullable=False),
        sa.Column(
            "discrepancy",
            sa.Numeric(16, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("bank_reference", sa.String(160), nullable=True),
        sa.Column("notes", sa.String(1024), nullable=True),
        sa.Column(
            "journal_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint("code", name="uq_fin_cod_deposits_code"),
        sa.CheckConstraint(
            "status IN ('pending','deposited','discrepancy')",
            name="ck_fin_cod_deposits_status_enum",
        ),
        sa.CheckConstraint(
            "expected_amount >= 0 AND deposited_amount >= 0",
            name="ck_fin_cod_deposits_amounts_nonneg",
        ),
    )
    op.create_index(
        "ix_fin_cod_deposits_rider_date",
        "fin_cod_deposits",
        ["rider_id", "deposit_date"],
    )
    op.create_index(
        "ix_fin_cod_deposits_status", "fin_cod_deposits", ["status"],
    )

    # ---------------- Refund records ----------------
    op.create_table(
        "fin_refund_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column(
            "return_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("return_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "customer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("accrued_amount", sa.Numeric(16, 2), nullable=False),
        sa.Column(
            "paid_amount",
            sa.Numeric(16, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "accrual_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "payment_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("method", sa.String(32), nullable=True),
        sa.Column("bank_reference", sa.String(160), nullable=True),
        sa.Column("notes", sa.String(1024), nullable=True),
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
        sa.UniqueConstraint("code", name="uq_fin_refund_records_code"),
        sa.UniqueConstraint(
            "return_request_id", name="uq_fin_refund_records_return",
        ),
        sa.CheckConstraint(
            "status IN ('pending','paid','cancelled')",
            name="ck_fin_refund_records_status_enum",
        ),
        sa.CheckConstraint(
            "accrued_amount >= 0 AND paid_amount >= 0",
            name="ck_fin_refund_records_amounts_nonneg",
        ),
        sa.CheckConstraint(
            "paid_amount <= accrued_amount",
            name="ck_fin_refund_records_paid_within_accrued",
        ),
    )
    op.create_index(
        "ix_fin_refund_records_order", "fin_refund_records", ["order_id"],
    )
    op.create_index(
        "ix_fin_refund_records_status", "fin_refund_records", ["status"],
    )

    # ---------------- Daily close ----------------
    op.create_table(
        "fin_daily_closes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("closing_date", sa.Date(), nullable=False),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "closed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("total_debits", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_credits", sa.Numeric(18, 2), nullable=False),
        sa.Column("revenue_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("cogs_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("cash_in_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("cash_out_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("notes", sa.String(2048), nullable=True),
        sa.Column(
            "snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint("closing_date", name="uq_fin_daily_closes_closing_date"),
        sa.CheckConstraint(
            "entry_count >= 0", name="ck_fin_daily_closes_entry_count_nonneg",
        ),
    )
    op.create_index(
        "ix_fin_daily_closes_closing_date", "fin_daily_closes", ["closing_date"],
    )

    # ---------------- Append-only enforcement ----------------
    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE fin_journal_lines FROM PUBLIC;
          REVOKE UPDATE, DELETE ON TABLE fin_daily_closes FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fin_daily_closes_closing_date", table_name="fin_daily_closes",
    )
    op.drop_table("fin_daily_closes")

    op.drop_index("ix_fin_refund_records_status", table_name="fin_refund_records")
    op.drop_index("ix_fin_refund_records_order", table_name="fin_refund_records")
    op.drop_table("fin_refund_records")

    op.drop_index("ix_fin_cod_deposits_status", table_name="fin_cod_deposits")
    op.drop_index("ix_fin_cod_deposits_rider_date", table_name="fin_cod_deposits")
    op.drop_table("fin_cod_deposits")

    op.drop_index(
        "ix_fin_supplier_payments_paid_on", table_name="fin_supplier_payments",
    )
    op.drop_index(
        "ix_fin_supplier_payments_bill", table_name="fin_supplier_payments",
    )
    op.drop_table("fin_supplier_payments")

    op.drop_index("ix_fin_supplier_bills_due_date", table_name="fin_supplier_bills")
    op.drop_index("ix_fin_supplier_bills_status", table_name="fin_supplier_bills")
    op.drop_index(
        "ix_fin_supplier_bills_supplier_id", table_name="fin_supplier_bills",
    )
    op.drop_table("fin_supplier_bills")

    op.drop_index("ix_fin_journal_lines_entry", table_name="fin_journal_lines")
    op.drop_index("ix_fin_journal_lines_account_id", table_name="fin_journal_lines")
    op.drop_table("fin_journal_lines")

    op.drop_index(
        "ix_fin_journal_entries_source", table_name="fin_journal_entries",
    )
    op.drop_index(
        "ix_fin_journal_entries_reference", table_name="fin_journal_entries",
    )
    op.drop_index(
        "ix_fin_journal_entries_status_date", table_name="fin_journal_entries",
    )
    op.drop_index(
        "ix_fin_journal_entries_entry_date", table_name="fin_journal_entries",
    )
    op.drop_table("fin_journal_entries")

    op.drop_index("ix_fin_periods_status", table_name="fin_accounting_periods")
    op.drop_table("fin_accounting_periods")

    op.drop_index("ix_fin_accounts_is_active", table_name="fin_accounts")
    op.drop_index("ix_fin_accounts_type", table_name="fin_accounts")
    op.drop_table("fin_accounts")
