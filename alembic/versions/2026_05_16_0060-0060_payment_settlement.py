"""0060 payment_settlement — gateway settlement reconciliation tables.

Two new tables that back the ``payments.reconcile`` short-form perm
endpoint at POST /api/v1/admin/payments/reconcile:

  payment_settlement_reports  — header per (provider, business date)
  payment_settlement_matches  — one row per settlement line; matched
                                to a PaymentIntent by provider_ref

Drift summary lives on the report row (drift_count, drift_total). A
downstream finance worker consumes the
``payment.reconciliation.completed`` outbox event to post the drift
summary as a finance.JournalEntry — the payments module deliberately
does NOT couple to finance internals so each side can evolve.

Idempotency: ``(provider, report_date)`` is unique on the report
table; a re-upload for the same business day fails fast with a
constraint violation. ``(settlement_report_id, provider_ref)`` is
unique on matches so the same line can't be inserted twice within a
report.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

# revision identifiers, used by Alembic.
revision: str = "0060_payment_settlement"
down_revision: str | Sequence[str] | None = "0059_hypershop_wallet"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "payment_settlement_reports",
        sa.Column(
            "id", PgUUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("submitted_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("submitted_count", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "matched_count", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "drift_count", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "orphan_count", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "drift_total", sa.Numeric(14, 2), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "processed_by", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint(
            "provider", "report_date",
            name="uq_payment_settlement_reports_provider_date",
        ),
        sa.CheckConstraint(
            "status IN ('pending','processed','closed')",
            name="ck_payment_settlement_reports_status_enum",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_payment_settlement_reports_currency_iso",
        ),
        sa.CheckConstraint(
            "submitted_count >= 0",
            name="ck_payment_settlement_reports_count_nonneg",
        ),
    )
    op.create_index(
        "ix_payment_settlement_reports_date",
        "payment_settlement_reports", ["report_date"],
    )
    op.create_index(
        "ix_payment_settlement_reports_status",
        "payment_settlement_reports", ["status", "report_date"],
    )

    op.create_table(
        "payment_settlement_matches",
        sa.Column(
            "id", PgUUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "settlement_report_id", PgUUID(as_uuid=True),
            sa.ForeignKey(
                "payment_settlement_reports.id", ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "payment_intent_id", PgUUID(as_uuid=True),
            sa.ForeignKey(
                "payment_intents.id", ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column("provider_ref", sa.String(128), nullable=False),
        sa.Column("provider_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("intent_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "drift_amount", sa.Numeric(14, 2), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('matched','drift','orphan')",
            name="ck_payment_settlement_matches_status_enum",
        ),
        sa.UniqueConstraint(
            "settlement_report_id", "provider_ref",
            name="uq_payment_settlement_matches_report_ref",
        ),
    )
    op.create_index(
        "ix_payment_settlement_matches_report_status",
        "payment_settlement_matches",
        ["settlement_report_id", "status"],
    )
    op.create_index(
        "ix_payment_settlement_matches_intent",
        "payment_settlement_matches", ["payment_intent_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payment_settlement_matches_intent",
        table_name="payment_settlement_matches",
    )
    op.drop_index(
        "ix_payment_settlement_matches_report_status",
        table_name="payment_settlement_matches",
    )
    op.drop_table("payment_settlement_matches")
    op.drop_index(
        "ix_payment_settlement_reports_status",
        table_name="payment_settlement_reports",
    )
    op.drop_index(
        "ix_payment_settlement_reports_date",
        table_name="payment_settlement_reports",
    )
    op.drop_table("payment_settlement_reports")
