"""Delivery operations: riders, delivery_assignments, status history

Revision ID: 0012_deliveries
Revises: 0011_packing
Create Date: 2026-05-03

``delivery_status_history`` is locked down with REVOKE UPDATE/DELETE so a
service bug cannot rewrite a delivery transition log. Mirrors the
audit_log / stock_ledger / order_status_history defence pattern.

A partial unique index on ``delivery_assignments(order_id) WHERE status
IN ('assigned','picked_up','delivered')`` enforces "one active delivery
per order at a time" — cancelled/failed/completed coexist as history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_deliveries"
down_revision: str | Sequence[str] | None = "0011_packing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "riders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("vehicle_type", sa.String(16), nullable=False),
        sa.Column("license_number", sa.String(64), nullable=True),
        sa.Column(
            "linked_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "current_status",
            sa.String(16),
            nullable=False,
            server_default="offline",
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
        sa.UniqueConstraint("code", name="uq_riders_code"),
        sa.UniqueConstraint("linked_user_id", name="uq_riders_linked_user_id"),
        sa.CheckConstraint(
            "vehicle_type IN ('motorcycle','bicycle','van','other')",
            name="ck_riders_vehicle_type_enum",
        ),
        sa.CheckConstraint(
            "current_status IN ('offline','available','busy')",
            name="ck_riders_current_status_enum",
        ),
    )
    op.create_index("ix_riders_is_active", "riders", ["is_active"])

    op.create_table(
        "delivery_assignments",
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
            "rider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "assigned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(512), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(512), nullable=True),
        sa.Column("payment_method", sa.String(16), nullable=False),
        sa.Column(
            "cod_expected",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("cod_collected", sa.Numeric(14, 2), nullable=True),
        sa.Column("cod_status", sa.String(16), nullable=False),
        sa.Column("cod_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cod_reconciled_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cod_resolution_notes", sa.String(1024), nullable=True),
        sa.Column("pod_photo_path", sa.String(255), nullable=True),
        sa.Column("pod_signature_path", sa.String(255), nullable=True),
        sa.Column("pod_otp_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pod_recipient_name", sa.String(160), nullable=True),
        sa.Column("pod_notes", sa.String(2048), nullable=True),
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
            "status IN ('assigned','picked_up','delivered','completed','cancelled','failed')",
            name="ck_delivery_assignments_status_enum",
        ),
        sa.CheckConstraint(
            "payment_method IN ('cod','online')",
            name="ck_delivery_assignments_payment_method_enum",
        ),
        sa.CheckConstraint(
            "cod_status IN ('n/a','pending','reconciled','discrepancy','resolved')",
            name="ck_delivery_assignments_cod_status_enum",
        ),
        sa.CheckConstraint(
            "cod_expected >= 0", name="ck_delivery_assignments_cod_expected_nonneg",
        ),
        sa.CheckConstraint(
            "cod_collected IS NULL OR cod_collected >= 0",
            name="ck_delivery_assignments_cod_collected_nonneg",
        ),
    )
    op.create_index(
        "ix_delivery_assignments_order_id", "delivery_assignments", ["order_id"],
    )
    op.create_index(
        "ix_delivery_assignments_rider_id", "delivery_assignments", ["rider_id"],
    )
    op.create_index(
        "ix_delivery_assignments_status", "delivery_assignments", ["status"],
    )

    # At most one active delivery per order. Cancelled / failed / completed
    # coexist as history.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_delivery_assignments_one_active_per_order
        ON delivery_assignments (order_id)
        WHERE status IN ('assigned','picked_up','delivered')
        """,
    )

    op.create_table(
        "delivery_status_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "assignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column(
            "transitioned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "transitioned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.CheckConstraint(
            "to_status IN ('assigned','picked_up','delivered','completed','cancelled','failed')",
            name="ck_delivery_status_history_to_status_enum",
        ),
    )
    op.create_index(
        "ix_delivery_status_history_assignment",
        "delivery_status_history",
        ["assignment_id", "transitioned_at"],
    )

    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE delivery_status_history FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_delivery_status_history_assignment",
        table_name="delivery_status_history",
    )
    op.drop_table("delivery_status_history")

    op.execute("DROP INDEX IF EXISTS uq_delivery_assignments_one_active_per_order")
    op.drop_index(
        "ix_delivery_assignments_status", table_name="delivery_assignments",
    )
    op.drop_index(
        "ix_delivery_assignments_rider_id", table_name="delivery_assignments",
    )
    op.drop_index(
        "ix_delivery_assignments_order_id", table_name="delivery_assignments",
    )
    op.drop_table("delivery_assignments")

    op.drop_index("ix_riders_is_active", table_name="riders")
    op.drop_table("riders")
