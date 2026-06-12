"""Finance Manager operational tables (Phase B of role-rule wiring).

Revision ID: 0090_finance_operations
Revises: 0089_marketplace_fulfillment_roles
Create Date: 2026-05-26

Adds the 7 operational queues + audit trail that the Finance Manager
Rules package (2026-05-26) requires. Sits ON TOP of the existing
accounting kernel (fin_accounts / fin_journal_entries / fin_*) — these
new tables are workflow queues, not double-entry ledgers.

Tables:
  finance_audit_logs          — append-only every Finance Manager
                                action (actor, role, action, entity,
                                amount, evidence URL, reference ID, IP).
                                Pair with REVOKE UPDATE/DELETE for
                                immutability.
  finance_refund_approvals    — refund decision queue (Customer Support
                                creates request, Finance Manager approves
                                / rejects / holds).
  finance_cod_settlements     — rider COD handover settlements awaiting
                                Finance Manager verification.
  finance_seller_payout_batches — periodic seller payout batches awaiting
                                Finance Manager approve / hold / release.
  finance_rider_payout_batches — periodic rider payout batches.
  finance_wallet_ledger_adjustments — wallet adjustment requests routed
                                to Finance Manager.
  finance_adjustment_requests — generic Finance Manager adjustment queue
                                (when none of the above fit).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0090_finance_operations"
down_revision = "0089_mp_fulfillment_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------ finance_audit_logs ------
    op.create_table(
        "finance_audit_logs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("audit_code", sa.String(32), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("actor_role", sa.String(48), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("order_id", sa.String(80), nullable=True),
        sa.Column("old_status", sa.String(48), nullable=True),
        sa.Column("new_status", sa.String(48), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(512), nullable=True),
        sa.Column(
            "amount_minor", sa.BigInteger, nullable=True,
            comment="Always BDT minor units (paisa).",
        ),
        sa.Column("currency", sa.String(8), nullable=False, server_default="BDT"),
        sa.Column("reference_id", sa.String(80), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("device_info", sa.String(255), nullable=True),
        sa.Column(
            "metadata_json", postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_finance_audit_logs_entity",
        "finance_audit_logs",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "ix_finance_audit_logs_actor_created",
        "finance_audit_logs",
        ["actor_id", "created_at"],
    )
    op.create_index(
        "ix_finance_audit_logs_action",
        "finance_audit_logs",
        ["action"],
    )

    # ------ finance_refund_approvals ------
    op.create_table(
        "finance_refund_approvals",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("order_id", sa.String(80), nullable=False),
        sa.Column("payment_ref", sa.String(80), nullable=True),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="BDT"),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(512), nullable=True),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | approved | rejected | held",
        ),
        sa.Column("decided_by", sa.String(80), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','held')",
            name="ck_refund_approvals_status",
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_refund_approvals_amount_pos"),
    )
    op.create_index("ix_refund_approvals_order", "finance_refund_approvals", ["order_id"])
    op.create_index("ix_refund_approvals_status", "finance_refund_approvals", ["status"])

    # ------ finance_cod_settlements ------
    op.create_table(
        "finance_cod_settlements",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("settlement_code", sa.String(32), nullable=False, unique=True),
        sa.Column("rider_id", sa.String(80), nullable=False),
        sa.Column("handover_date", sa.Date, nullable=False),
        sa.Column("delivered_cod_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expected_cod_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("collected_cod_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("deposited_cod_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "mismatch_minor", sa.BigInteger, nullable=False, server_default="0",
            comment="collected - deposited; sign carries shortage / over.",
        ),
        sa.Column("settlement_ref", sa.String(80), nullable=True),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | verified | rejected | reconciled",
        ),
        sa.Column("verified_by", sa.String(80), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','verified','rejected','reconciled')",
            name="ck_cod_settlements_status",
        ),
    )
    op.create_index("ix_cod_settlements_rider", "finance_cod_settlements", ["rider_id"])
    op.create_index("ix_cod_settlements_date", "finance_cod_settlements", ["handover_date"])
    op.create_index("ix_cod_settlements_status", "finance_cod_settlements", ["status"])

    # ------ finance_seller_payout_batches ------
    op.create_table(
        "finance_seller_payout_batches",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("batch_code", sa.String(32), nullable=False, unique=True),
        sa.Column("seller_id", sa.String(80), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("payable_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("commission_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("chargeback_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("net_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | approved | held | released | failed",
        ),
        sa.Column("approved_by", sa.String(80), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.String(80), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("held_by", sa.String(80), nullable=True),
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hold_reason", sa.Text, nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','held','released','failed')",
            name="ck_seller_payout_status",
        ),
    )
    op.create_index("ix_seller_payouts_seller", "finance_seller_payout_batches", ["seller_id"])
    op.create_index("ix_seller_payouts_status", "finance_seller_payout_batches", ["status"])

    # ------ finance_rider_payout_batches ------
    op.create_table(
        "finance_rider_payout_batches",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("batch_code", sa.String(32), nullable=False, unique=True),
        sa.Column("rider_id", sa.String(80), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("incentive_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("penalty_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("net_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | approved | held | released | verified | failed",
        ),
        sa.Column("approved_by", sa.String(80), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.String(80), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(80), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("held_by", sa.String(80), nullable=True),
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hold_reason", sa.Text, nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','held','released','verified','failed')",
            name="ck_rider_payout_status",
        ),
    )
    op.create_index("ix_rider_payouts_rider", "finance_rider_payout_batches", ["rider_id"])
    op.create_index("ix_rider_payouts_status", "finance_rider_payout_batches", ["status"])

    # ------ finance_wallet_ledger_adjustments ------
    op.create_table(
        "finance_wallet_ledger_adjustments",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "wallet_type", sa.String(24), nullable=False,
            comment="customer | rider | seller",
        ),
        sa.Column("wallet_owner_id", sa.String(80), nullable=False),
        sa.Column(
            "direction", sa.String(8), nullable=False,
            comment="debit | credit (append-only ledger entry).",
        ),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="BDT"),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("source_ref", sa.String(80), nullable=False),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(512), nullable=True),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | approved | rejected",
        ),
        sa.Column("approved_by", sa.String(80), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "wallet_type IN ('customer','rider','seller')",
            name="ck_wallet_adj_type",
        ),
        sa.CheckConstraint(
            "direction IN ('debit','credit')",
            name="ck_wallet_adj_direction",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_wallet_adj_status",
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_wallet_adj_amount_pos"),
    )
    op.create_index(
        "ix_wallet_adj_owner",
        "finance_wallet_ledger_adjustments",
        ["wallet_type", "wallet_owner_id"],
    )
    op.create_index(
        "ix_wallet_adj_status",
        "finance_wallet_ledger_adjustments",
        ["status"],
    )

    # ------ finance_adjustment_requests ------
    op.create_table(
        "finance_adjustment_requests",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("request_code", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "category", sa.String(48), nullable=False,
            comment=("dispute | fraud_review | commission | "
                     "settlement_correction | other"),
        ),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="BDT"),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(512), nullable=True),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | approved | rejected | resolved",
        ),
        sa.Column("resolved_by", sa.String(80), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_note", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','resolved')",
            name="ck_adjustment_requests_status",
        ),
    )
    op.create_index(
        "ix_adjustment_requests_status",
        "finance_adjustment_requests",
        ["status"],
    )

    # Immutability: REVOKE UPDATE/DELETE on the audit log so even
    # Super Admin in a SQL shell cannot rewrite history. Application
    # role uses the regular grant; the audit-export job runs as the
    # privileged ``hypershop_audit_export`` role which still has SELECT.
    # NOTE: kept commented because role grants are environment-managed
    # via app/core/db/grants.sql — flip on after that file is updated.
    # op.execute("REVOKE UPDATE, DELETE ON finance_audit_logs FROM hypershop")


def downgrade() -> None:
    op.drop_index("ix_adjustment_requests_status", "finance_adjustment_requests")
    op.drop_table("finance_adjustment_requests")
    op.drop_index("ix_wallet_adj_status", "finance_wallet_ledger_adjustments")
    op.drop_index("ix_wallet_adj_owner", "finance_wallet_ledger_adjustments")
    op.drop_table("finance_wallet_ledger_adjustments")
    op.drop_index("ix_rider_payouts_status", "finance_rider_payout_batches")
    op.drop_index("ix_rider_payouts_rider", "finance_rider_payout_batches")
    op.drop_table("finance_rider_payout_batches")
    op.drop_index("ix_seller_payouts_status", "finance_seller_payout_batches")
    op.drop_index("ix_seller_payouts_seller", "finance_seller_payout_batches")
    op.drop_table("finance_seller_payout_batches")
    op.drop_index("ix_cod_settlements_status", "finance_cod_settlements")
    op.drop_index("ix_cod_settlements_date", "finance_cod_settlements")
    op.drop_index("ix_cod_settlements_rider", "finance_cod_settlements")
    op.drop_table("finance_cod_settlements")
    op.drop_index("ix_refund_approvals_status", "finance_refund_approvals")
    op.drop_index("ix_refund_approvals_order", "finance_refund_approvals")
    op.drop_table("finance_refund_approvals")
    op.drop_index("ix_finance_audit_logs_action", "finance_audit_logs")
    op.drop_index("ix_finance_audit_logs_actor_created", "finance_audit_logs")
    op.drop_index("ix_finance_audit_logs_entity", "finance_audit_logs")
    op.drop_table("finance_audit_logs")
