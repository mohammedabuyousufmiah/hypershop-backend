"""0054 subscriptions — Module 49: recurring orders.

Two tables:
- subscriptions          — customer's standing order: variant + cadence + status + next_charge_at
- subscription_cycles    — one row per fulfilment cycle (links to a real orders.id)

State machine:
  pending_activation → active (after first cycle successfully charges)
  active ↔ paused (customer or admin)
  active → cancelled (terminal)
  active → payment_failed (auto-set after 3 consecutive charge failures; admin can unpause)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0054_subscriptions"
down_revision: str | Sequence[str] | None = "0053_marketing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("customer_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("variant_id", UUID(as_uuid=True),
                  sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("cadence", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False,
                  server_default="pending_activation"),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("delivery_address", sa.JSON, nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="BDT"),
        sa.Column("next_charge_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("last_charged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer,
                  nullable=False, server_default="0"),
        sa.Column("cycle_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("paused_reason", sa.String(500), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "cadence IN ('weekly','biweekly','monthly','quarterly')",
            name="ck_subscriptions_cadence",
        ),
        sa.CheckConstraint(
            "status IN ('pending_activation','active','paused','cancelled','payment_failed')",
            name="ck_subscriptions_status",
        ),
        sa.CheckConstraint(
            "payment_method IN ('cod','online')",
            name="ck_subscriptions_payment_method",
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_subscriptions_qty_positive",
        ),
    )
    op.create_index(
        "ix_subscriptions_due",
        "subscriptions", ["status", "next_charge_at"],
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "subscription_cycles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("subscription_id", UUID(as_uuid=True),
                  sa.ForeignKey("subscriptions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("cycle_number", sa.Integer, nullable=False),
        sa.Column("order_id", UUID(as_uuid=True),
                  sa.ForeignKey("orders.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("charged_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('order_placed','order_failed')",
            name="ck_subscription_cycles_status",
        ),
        # One cycle per (subscription, cycle_number)
        sa.UniqueConstraint(
            "subscription_id", "cycle_number",
            name="uq_subscription_cycles_subscription_cycle",
        ),
    )


def downgrade() -> None:
    op.drop_table("subscription_cycles")
    op.drop_index("ix_subscriptions_due", table_name="subscriptions")
    op.drop_table("subscriptions")
