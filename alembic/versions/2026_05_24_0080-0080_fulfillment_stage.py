"""21-state fulfillment_stage sub-status on orders

Revision ID: 0080_fulfillment_stage
Revises: 0079_seo_autogen_flag
Create Date: 2026-05-24

Adds a parallel 21-state fulfillment lifecycle as a sub-status on the
existing 9-state ``orders.status`` machine. The legal/financial truth
stays on ``status``; the ops/rider/hub UI consumes ``fulfillment_stage``
which models the detailed last-mile flow:

    ORDER_PLACED -> PAYMENT_PENDING -> PAYMENT_VERIFIED / COD_ACCEPTED
        -> SELLER_PROCESSING -> READY_TO_SHIP
        -> PICKUP_ASSIGNED -> PICKED_UP -> AT_HUB
        -> SORTED_FOR_DELIVERY -> RIDER_ASSIGNED -> OUT_FOR_DELIVERY
        -> DELIVERY_ATTEMPTED -> DELIVERED  (happy path)
                              -> FAILED_DELIVERY -> RESCHEDULED  (retry loop)
                                                 -> RETURNING_TO_HUB
                                                       -> RETURNED_TO_HUB
                                                       -> RETURNING_TO_SELLER
                                                       -> RETURNED_TO_SELLER
        terminal alt: CANCELLED, REFUNDED

Stage history is logged into a dedicated append-only table
``order_fulfillment_stage_history`` (mirrors order_status_history but
without contaminating the legal status log).

Backfill maps existing orders.status -> nearest stage so historical rows
are not orphaned.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0080_fulfillment_stage"
down_revision = "0079_seo_autogen_flag"
branch_labels = None
depends_on = None


STAGE_VALUES = (
    "ORDER_PLACED",
    "PAYMENT_PENDING",
    "PAYMENT_VERIFIED",
    "COD_ACCEPTED",
    "SELLER_PROCESSING",
    "READY_TO_SHIP",
    "PICKUP_ASSIGNED",
    "PICKED_UP",
    "AT_HUB",
    "SORTED_FOR_DELIVERY",
    "RIDER_ASSIGNED",
    "OUT_FOR_DELIVERY",
    "DELIVERY_ATTEMPTED",
    "DELIVERED",
    "FAILED_DELIVERY",
    "RESCHEDULED",
    "RETURNING_TO_HUB",
    "RETURNED_TO_HUB",
    "RETURNING_TO_SELLER",
    "RETURNED_TO_SELLER",
    "CANCELLED",
    "REFUNDED",
)


def _check_expr() -> str:
    inner = ",".join(f"'{v}'" for v in STAGE_VALUES)
    return f"fulfillment_stage IN ({inner})"


def _check_expr_hist() -> str:
    inner = ",".join(f"'{v}'" for v in STAGE_VALUES)
    return f"to_stage IN ({inner})"


def upgrade() -> None:
    # 1. Add nullable column on orders.
    op.add_column(
        "orders",
        sa.Column("fulfillment_stage", sa.String(40), nullable=True),
    )

    # 2. Backfill: map current 9-state status -> nearest 21-state stage.
    op.execute(
        """
        UPDATE orders SET fulfillment_stage = CASE status
            WHEN 'pending_payment'    THEN 'PAYMENT_PENDING'
            WHEN 'payment_confirmed'  THEN 'PAYMENT_VERIFIED'
            WHEN 'stock_reserved'     THEN 'SELLER_PROCESSING'
            WHEN 'approved'           THEN 'SELLER_PROCESSING'
            WHEN 'packing'            THEN 'READY_TO_SHIP'
            WHEN 'out_for_delivery'   THEN 'OUT_FOR_DELIVERY'
            WHEN 'completed'          THEN 'DELIVERED'
            WHEN 'cancelled'          THEN 'CANCELLED'
            WHEN 'failed'             THEN 'CANCELLED'
            ELSE 'ORDER_PLACED'
        END
        """
    )

    # 3. Enforce NOT NULL + CHECK now that all rows are populated.
    op.alter_column("orders", "fulfillment_stage", nullable=False)
    op.create_check_constraint(
        "fulfillment_stage_enum", "orders", _check_expr(),
    )
    op.create_index(
        "ix_orders_fulfillment_stage", "orders", ["fulfillment_stage"],
    )

    # 4. Dedicated stage-history table (append-only).
    op.create_table(
        "order_fulfillment_stage_history",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_stage", sa.String(40), nullable=True),
        sa.Column("to_stage", sa.String(40), nullable=False),
        sa.Column(
            "transitioned_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "transitioned_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column(
            "meta",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.CheckConstraint(_check_expr_hist(), name="to_stage_enum"),
    )
    op.create_index(
        "ix_ofsh_order_time",
        "order_fulfillment_stage_history",
        ["order_id", "transitioned_at"],
    )

    # 5. Defence-in-depth: revoke UPDATE/DELETE from PUBLIC on the
    #    history table (mirrors order_status_history + audit_log).
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE order_fulfillment_stage_history "
        "FROM PUBLIC"
    )


def downgrade() -> None:
    op.drop_index("ix_ofsh_order_time", table_name="order_fulfillment_stage_history")
    op.drop_table("order_fulfillment_stage_history")
    op.drop_index("ix_orders_fulfillment_stage", table_name="orders")
    op.drop_constraint("fulfillment_stage_enum", "orders", type_="check")
    op.drop_column("orders", "fulfillment_stage")
