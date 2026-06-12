"""marketplace_order_audit_logs — per-order strict audit trail

Revision ID: 0088_order_audit_logs
Revises: 0087_seller_penalties
Create Date: 2026-05-24

10-field canonical per-order audit table separate from the generic
marketplace_dispatch_audit_logs. Every order-mutating action across the
fulfillment domain writes one row here with strict typed columns +
proof_url + actor_role snapshot (since user's role may change later).

    actor_id, actor_role, order_id, action_type, old_value, new_value,
    reason, proof_url, ip_address, created_at

REVOKE UPDATE/DELETE = append-only forensic trail.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0088_order_audit_logs"
down_revision = "0087_seller_penalties"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketplace_order_audit_logs",
        sa.Column(
            "id", pg.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "actor_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_role", sa.String(40), nullable=True,
            comment="Role snapshot at action time (admin/dispatcher/etc)",
        ),
        sa.Column(
            "order_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(60), nullable=False),
        sa.Column("old_value", pg.JSONB(), nullable=True),
        sa.Column("new_value", pg.JSONB(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("proof_url", sa.String(500), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_oal_order_time",
        "marketplace_order_audit_logs",
        ["order_id", "created_at"],
    )
    op.create_index(
        "ix_oal_actor_time",
        "marketplace_order_audit_logs",
        ["actor_id", "created_at"],
    )
    op.create_index(
        "ix_oal_action_time",
        "marketplace_order_audit_logs",
        ["action_type", "created_at"],
    )
    # Defence-in-depth: append-only (mirrors audit_log + stage_history +
    # dispatch_audit_logs + seller_penalties pattern).
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE marketplace_order_audit_logs "
        "FROM PUBLIC"
    )


def downgrade() -> None:
    op.drop_index("ix_oal_action_time", table_name="marketplace_order_audit_logs")
    op.drop_index("ix_oal_actor_time", table_name="marketplace_order_audit_logs")
    op.drop_index("ix_oal_order_time", table_name="marketplace_order_audit_logs")
    op.drop_table("marketplace_order_audit_logs")
