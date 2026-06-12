"""Online payments — Bkash + SSLCommerz tables.

Revision ID: 0020_payments
Revises: 0017_mobile
Create Date: 2026-05-04

Tables introduced:
- payment_intents:  one per attempt (customer retry → fresh intent)
- payment_attempts: per-call gateway audit trail
- payment_webhooks: verbatim webhook ingestion log (for replay)
- payment_refunds:  refund operations against captured intents
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_payments"
down_revision: str | Sequence[str] | None = "0017_mobile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- payment_intents ----------------
    op.create_table(
        "payment_intents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
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
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_payment_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "amount_captured",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "amount_refunded",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("checkout_url", sa.String(2048), nullable=True),
        sa.Column("success_url", sa.String(2048), nullable=True),
        sa.Column("failure_url", sa.String(2048), nullable=True),
        sa.Column("cancel_url", sa.String(2048), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "initiated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_message", sa.String(2048), nullable=True),
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
            "status IN ('initiated','authorized','captured','failed',"
            "'cancelled','expired','refunded','partially_refunded')",
            name="ck_payment_intents_status_enum",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_payment_intents_currency_iso",
        ),
        sa.CheckConstraint("amount > 0", name="ck_payment_intents_amount_pos"),
        sa.CheckConstraint(
            "amount_captured >= 0 AND amount_captured <= amount",
            name="ck_payment_intents_captured_bounds",
        ),
        sa.CheckConstraint(
            "amount_refunded >= 0 AND amount_refunded <= amount_captured",
            name="ck_payment_intents_refund_bounds",
        ),
        sa.UniqueConstraint(
            "provider", "provider_payment_id",
            name="uq_payment_intents_provider_payment_id",
        ),
    )
    op.create_index("ix_payment_intents_order", "payment_intents", ["order_id"])
    op.create_index(
        "ix_payment_intents_customer_status",
        "payment_intents",
        ["customer_user_id", "status"],
    )
    op.create_index(
        "ix_payment_intents_status_initiated_at",
        "payment_intents",
        ["status", "initiated_at"],
    )

    # ---------------- payment_attempts ----------------
    op.create_table(
        "payment_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payment_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("request_summary", postgresql.JSONB(), nullable=True),
        sa.Column("response_summary", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(2048), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
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
            "status IN ('ok','failed','timeout','pending')",
            name="ck_payment_attempts_status_enum",
        ),
    )
    op.create_index(
        "ix_payment_attempts_intent_created",
        "payment_attempts",
        ["intent_id", "created_at"],
    )

    # ---------------- payment_webhooks ----------------
    op.create_table(
        "payment_webhooks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column(
            "intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payment_intents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("raw_body", postgresql.JSONB(), nullable=False),
        sa.Column("raw_headers", postgresql.JSONB(), nullable=False),
        sa.Column("rejection_reason", sa.String(512), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('received','processed','rejected','duplicate')",
            name="ck_payment_webhooks_status_enum",
        ),
        sa.UniqueConstraint(
            "provider", "event_id",
            name="uq_payment_webhooks_provider_event_id",
        ),
    )
    op.create_index(
        "ix_payment_webhooks_intent",
        "payment_webhooks",
        ["intent_id"],
    )
    op.create_index(
        "ix_payment_webhooks_status_received",
        "payment_webhooks",
        ["status", "received_at"],
    )

    # ---------------- payment_refunds ----------------
    op.create_table(
        "payment_refunds",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payment_intents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "return_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("return_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column("provider_refund_id", sa.String(128), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(2048), nullable=True),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('requested','succeeded','failed')",
            name="ck_payment_refunds_status_enum",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_payment_refunds_currency_iso",
        ),
        sa.CheckConstraint("amount > 0", name="ck_payment_refunds_amount_pos"),
    )
    op.create_index("ix_payment_refunds_intent", "payment_refunds", ["intent_id"])
    op.create_index(
        "ix_payment_refunds_status_created",
        "payment_refunds",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_refunds_status_created", table_name="payment_refunds")
    op.drop_index("ix_payment_refunds_intent", table_name="payment_refunds")
    op.drop_table("payment_refunds")

    op.drop_index("ix_payment_webhooks_status_received", table_name="payment_webhooks")
    op.drop_index("ix_payment_webhooks_intent", table_name="payment_webhooks")
    op.drop_table("payment_webhooks")

    op.drop_index("ix_payment_attempts_intent_created", table_name="payment_attempts")
    op.drop_table("payment_attempts")

    op.drop_index(
        "ix_payment_intents_status_initiated_at",
        table_name="payment_intents",
    )
    op.drop_index(
        "ix_payment_intents_customer_status",
        table_name="payment_intents",
    )
    op.drop_index("ix_payment_intents_order", table_name="payment_intents")
    op.drop_table("payment_intents")
