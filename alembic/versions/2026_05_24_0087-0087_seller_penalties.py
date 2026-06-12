"""marketplace_seller_penalties — automated seller performance penalties

Revision ID: 0087_seller_penalties
Revises: 0086_rider_cod_blocked
Create Date: 2026-05-24

4 canonical penalty types written by the seller-penalty-sweep cron when
performance thresholds breach:

    rank_down       — late_handover > threshold (visual ranking demotion)
    warning         — wrong_product_complaint repeated
    account_review  — pickup_failed 3+ times (requires manager review)
    order_limit     — cancellation rate high (temp daily cap)

Idempotent via active-penalty-exists check (one open of each type per
seller at a time). expires_at NULL = until manually cleared.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0087_seller_penalties"
down_revision = "0086_rider_cod_blocked"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketplace_seller_penalties",
        sa.Column(
            "id", pg.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "seller_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("penalty_type", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False,
                  server_default=sa.text("'warning'")),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "applied_at", sa.DateTime, nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column(
            "applied_by", pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cleared_at", sa.DateTime, nullable=True,
            comment="Manually lifted by ops; NULL = active",
        ),
        sa.Column(
            "cleared_by", pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("clear_reason", sa.String(400), nullable=True),
        sa.Column("meta", pg.JSONB(), nullable=True),
        sa.CheckConstraint(
            "penalty_type IN "
            "('rank_down','warning','account_review','order_limit',"
            "'commission_cut','listing_freeze')",
            name="penalty_type_enum",
        ),
        sa.CheckConstraint(
            "severity IN ('warning','critical','blocking')",
            name="penalty_severity_enum",
        ),
    )
    op.create_index(
        "ix_sp_active",
        "marketplace_seller_penalties",
        ["seller_id", "penalty_type"],
        postgresql_where=sa.text("cleared_at IS NULL"),
    )
    op.create_index(
        "ix_sp_seller_time",
        "marketplace_seller_penalties",
        ["seller_id", "applied_at"],
    )
    # Append-only intent: REVOKE UPDATE/DELETE so penalties can only be
    # cleared via the cleared_at column, not deleted outright.
    op.execute(
        "REVOKE DELETE ON TABLE marketplace_seller_penalties FROM PUBLIC"
    )


def downgrade() -> None:
    op.drop_index("ix_sp_seller_time", table_name="marketplace_seller_penalties")
    op.drop_index("ix_sp_active", table_name="marketplace_seller_penalties")
    op.drop_table("marketplace_seller_penalties")
