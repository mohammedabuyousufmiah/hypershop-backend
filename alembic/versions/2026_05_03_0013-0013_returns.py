"""Returns: requests, lines, status history

Revision ID: 0013_returns
Revises: 0012_deliveries
Create Date: 2026-05-03

``return_status_history`` is locked down with REVOKE UPDATE/DELETE so a
service bug cannot rewrite a return transition log.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_returns"
down_revision: str | Sequence[str] | None = "0012_deliveries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUS_VALUES = (
    "requested",
    "received",
    "inspected",
    "completed",
    "rejected",
    "cancelled",
)
_STATUS_CK = "status IN (" + ",".join(f"'{s}'" for s in _STATUS_VALUES) + ")"
_TO_STATUS_CK = "to_status IN (" + ",".join(f"'{s}'" for s in _STATUS_VALUES) + ")"


def upgrade() -> None:
    op.create_table(
        "return_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
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
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(2048), nullable=False),
        sa.Column("rejection_reason", sa.String(512), nullable=True),
        sa.Column("cancellation_reason", sa.String(512), nullable=True),
        sa.Column("notes", sa.String(2048), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("inspected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "inspected_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("code", name="uq_return_requests_code"),
        sa.CheckConstraint(_STATUS_CK, name="ck_return_requests_status_enum"),
    )
    op.create_index("ix_return_requests_order_id", "return_requests", ["order_id"])
    op.create_index(
        "ix_return_requests_customer_user_id",
        "return_requests",
        ["customer_user_id"],
    )
    op.create_index("ix_return_requests_status", "return_requests", ["status"])

    op.create_table(
        "return_request_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "return_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("return_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("order_lines.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("variant_sku", sa.String(64), nullable=False),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("inspected_quantity", sa.Integer(), nullable=True),
        sa.Column("condition", sa.String(20), nullable=True),
        sa.Column(
            "target_batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "target_warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("inspection_notes", sa.String(1024), nullable=True),
        sa.Column("applied_action", sa.String(16), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
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
            "requested_quantity > 0",
            name="ck_return_request_lines_requested_qty_positive",
        ),
        sa.CheckConstraint(
            "inspected_quantity IS NULL OR inspected_quantity >= 0",
            name="ck_return_request_lines_inspected_qty_nonneg",
        ),
        sa.CheckConstraint(
            "condition IS NULL OR condition IN "
            "('sealed','opened','cold_chain_broken','expired')",
            name="ck_return_request_lines_condition_enum",
        ),
        sa.CheckConstraint(
            "applied_action IS NULL OR applied_action IN "
            "('restock','block','dispose')",
            name="ck_return_request_lines_applied_action_enum",
        ),
    )
    op.create_index(
        "ix_return_request_lines_request_id",
        "return_request_lines",
        ["return_request_id"],
    )
    op.create_index(
        "ix_return_request_lines_variant_id",
        "return_request_lines",
        ["variant_id"],
    )

    op.create_table(
        "return_status_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "return_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("return_requests.id", ondelete="CASCADE"),
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
        sa.CheckConstraint(_TO_STATUS_CK, name="ck_return_status_history_to_status_enum"),
    )
    op.create_index(
        "ix_return_status_history_request",
        "return_status_history",
        ["return_request_id", "transitioned_at"],
    )

    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE return_status_history FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_return_status_history_request", table_name="return_status_history",
    )
    op.drop_table("return_status_history")

    op.drop_index(
        "ix_return_request_lines_variant_id", table_name="return_request_lines",
    )
    op.drop_index(
        "ix_return_request_lines_request_id", table_name="return_request_lines",
    )
    op.drop_table("return_request_lines")

    op.drop_index("ix_return_requests_status", table_name="return_requests")
    op.drop_index(
        "ix_return_requests_customer_user_id", table_name="return_requests",
    )
    op.drop_index("ix_return_requests_order_id", table_name="return_requests")
    op.drop_table("return_requests")
