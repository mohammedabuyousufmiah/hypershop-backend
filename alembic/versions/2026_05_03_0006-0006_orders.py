"""Orders: orders, order_lines, order_status_history

Revision ID: 0006_orders
Revises: 0005_inventory
Create Date: 2026-05-03

``order_status_history`` is locked down with REVOKE UPDATE, DELETE so a
service bug cannot rewrite a transition log. Same defence as ``audit_log``
and ``stock_ledger``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_orders"
down_revision: str | Sequence[str] | None = "0005_inventory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUS_VALUES = (
    "pending_payment",
    "payment_confirmed",
    "stock_reserved",
    "prescription_review",
    "approved",
    "packing",
    "out_for_delivery",
    "completed",
    "cancelled",
    "failed",
)
_STATUS_CK = "status IN (" + ",".join(f"'{s}'" for s in _STATUS_VALUES) + ")"
_TO_STATUS_CK = "to_status IN (" + ",".join(f"'{s}'" for s in _STATUS_VALUES) + ")"


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column(
            "customer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payment_method", sa.String(16), nullable=False),
        sa.Column(
            "requires_prescription",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "tax_total", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "shipping_total",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "discount_total",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("grand_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("delivery_address", postgresql.JSONB(), nullable=False),
        sa.Column("notes", sa.String(2048), nullable=True),
        sa.Column(
            "placed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("payment_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(512), nullable=True),
        sa.Column(
            "assigned_pharmacist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint("code", name="uq_orders_code"),
        sa.CheckConstraint(_STATUS_CK, name="ck_orders_status_enum"),
        sa.CheckConstraint(
            "payment_method IN ('cod','online')",
            name="ck_orders_payment_method_enum",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_orders_currency_iso",
        ),
        sa.CheckConstraint("subtotal >= 0", name="ck_orders_subtotal_nonneg"),
        sa.CheckConstraint("grand_total >= 0", name="ck_orders_grand_total_nonneg"),
    )
    op.create_index("ix_orders_customer_status", "orders", ["customer_user_id", "status"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_placed_at", "orders", ["placed_at"])

    op.create_table(
        "order_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
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
        sa.Column(
            "requires_prescription",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
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
        sa.CheckConstraint("quantity > 0", name="ck_order_lines_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_lines_unit_price_nonneg"),
        sa.CheckConstraint("line_total >= 0", name="ck_order_lines_line_total_nonneg"),
    )
    op.create_index("ix_order_lines_order_id", "order_lines", ["order_id"])
    op.create_index("ix_order_lines_variant_id", "order_lines", ["variant_id"])

    op.create_table(
        "order_status_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
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
        sa.CheckConstraint(_TO_STATUS_CK, name="ck_order_status_history_to_status_enum"),
    )
    op.create_index(
        "ix_order_status_history_order",
        "order_status_history",
        ["order_id", "transitioned_at"],
    )

    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE order_status_history FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )


def downgrade() -> None:
    op.drop_index("ix_order_status_history_order", table_name="order_status_history")
    op.drop_table("order_status_history")

    op.drop_index("ix_order_lines_variant_id", table_name="order_lines")
    op.drop_index("ix_order_lines_order_id", table_name="order_lines")
    op.drop_table("order_lines")

    op.drop_index("ix_orders_placed_at", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_customer_status", table_name="orders")
    op.drop_table("orders")
