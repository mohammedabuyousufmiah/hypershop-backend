"""0072 disputes_module — buyer/seller/mediator dispute resolution + escrow.

Four tables (all prefixed ``hypershop_``):
  hypershop_disputes           — core dispute record (one per order/item)
  hypershop_dispute_messages   — conversation thread (BIGINT id, high volume)
  hypershop_dispute_evidence   — uploaded attachments (URLs pre-uploaded to R2)
  hypershop_escrow_holds       — money locked on seller while dispute is live

Money in BIGINT minor (paisa). Messages use BIGINT identity (high volume);
disputes/evidence/holds use UUID. CHECK constraints enforce status enums,
amount non-negativity, and the escrow release-sum invariant.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0072_disputes_module"
down_revision: str | Sequence[str] | None = "0071_winback_persist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── hypershop_disputes ───────────────────────────────────────
    op.create_table(
        "hypershop_disputes",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("order_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("order_item_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("opened_by_user_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("seller_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("dispute_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("resolution", sa.String(32), nullable=True),
        sa.Column("amount_disputed_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "amount_refunded_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mediator_user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("last_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            "dispute_type IN ('wrong_item','damaged','not_received',"
            "'quality_issue','fake_item','billing_error','other')",
            name="ck_hypershop_disputes_type",
        ),
        sa.CheckConstraint(
            "status IN ('open','awaiting_seller','awaiting_buyer',"
            "'under_review','resolved','closed')",
            name="ck_hypershop_disputes_status",
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('refund_full',"
            "'refund_partial','replace','decline','customer_withdrew')",
            name="ck_hypershop_disputes_resolution",
        ),
        sa.CheckConstraint(
            "amount_disputed_minor >= 0",
            name="ck_hypershop_disputes_amount_disputed_nonneg",
        ),
        sa.CheckConstraint(
            "amount_refunded_minor >= 0",
            name="ck_hypershop_disputes_amount_refunded_nonneg",
        ),
    )
    op.create_index(
        "ix_hypershop_disputes_seller_status_at",
        "hypershop_disputes",
        ["seller_id", "status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_hypershop_disputes_buyer_at",
        "hypershop_disputes",
        ["opened_by_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_hypershop_disputes_awaiting_response",
        "hypershop_disputes",
        ["status", "last_response_at"],
        postgresql_where=sa.text(
            "status IN ('awaiting_seller','awaiting_buyer')",
        ),
    )
    op.create_index(
        "ix_hypershop_disputes_order_id",
        "hypershop_disputes",
        ["order_id"],
    )

    # ─── hypershop_dispute_messages ───────────────────────────────
    op.create_table(
        "hypershop_dispute_messages",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "dispute_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_disputes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_user_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("author_role", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "attachments",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "author_role IN ('buyer','seller','mediator','system')",
            name="ck_hypershop_dispute_messages_role",
        ),
    )
    op.create_index(
        "ix_hypershop_dispute_messages_dispute_at",
        "hypershop_dispute_messages",
        ["dispute_id", sa.text("created_at DESC")],
    )

    # ─── hypershop_dispute_evidence ───────────────────────────────
    op.create_table(
        "hypershop_dispute_evidence",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "dispute_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_disputes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by_user_id", PgUUID(as_uuid=True), nullable=False,
        ),
        sa.Column("uploader_role", sa.String(16), nullable=False),
        sa.Column("file_url", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "uploader_role IN ('buyer','seller','mediator')",
            name="ck_hypershop_dispute_evidence_role",
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name="ck_hypershop_dispute_evidence_size_pos",
        ),
    )
    op.create_index(
        "ix_hypershop_dispute_evidence_dispute_at",
        "hypershop_dispute_evidence",
        ["dispute_id", sa.text("created_at DESC")],
    )

    # ─── hypershop_escrow_holds ───────────────────────────────────
    op.create_table(
        "hypershop_escrow_holds",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "dispute_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_disputes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seller_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("order_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("held_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "released_to_buyer_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "released_to_seller_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("release_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "dispute_id", name="uq_hypershop_escrow_holds_dispute",
        ),
        sa.CheckConstraint(
            "held_amount_minor >= 0",
            name="ck_hypershop_escrow_holds_held_nonneg",
        ),
        sa.CheckConstraint(
            "status IN ('active','released_to_buyer','released_to_seller',"
            "'split','cancelled')",
            name="ck_hypershop_escrow_holds_status",
        ),
        sa.CheckConstraint(
            "released_to_buyer_minor + released_to_seller_minor "
            "<= held_amount_minor",
            name="ck_hypershop_escrow_holds_release_sum",
        ),
    )
    op.create_index(
        "ix_hypershop_escrow_holds_seller_status",
        "hypershop_escrow_holds",
        ["seller_id", "status"],
    )
    op.create_index(
        "ix_hypershop_escrow_holds_active_at",
        "hypershop_escrow_holds",
        ["status", "created_at"],
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hypershop_escrow_holds_active_at",
        table_name="hypershop_escrow_holds",
    )
    op.drop_index(
        "ix_hypershop_escrow_holds_seller_status",
        table_name="hypershop_escrow_holds",
    )
    op.drop_table("hypershop_escrow_holds")

    op.drop_index(
        "ix_hypershop_dispute_evidence_dispute_at",
        table_name="hypershop_dispute_evidence",
    )
    op.drop_table("hypershop_dispute_evidence")

    op.drop_index(
        "ix_hypershop_dispute_messages_dispute_at",
        table_name="hypershop_dispute_messages",
    )
    op.drop_table("hypershop_dispute_messages")

    op.drop_index(
        "ix_hypershop_disputes_order_id",
        table_name="hypershop_disputes",
    )
    op.drop_index(
        "ix_hypershop_disputes_awaiting_response",
        table_name="hypershop_disputes",
    )
    op.drop_index(
        "ix_hypershop_disputes_buyer_at",
        table_name="hypershop_disputes",
    )
    op.drop_index(
        "ix_hypershop_disputes_seller_status_at",
        table_name="hypershop_disputes",
    )
    op.drop_table("hypershop_disputes")
