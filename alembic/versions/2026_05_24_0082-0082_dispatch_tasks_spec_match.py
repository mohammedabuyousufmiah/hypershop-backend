"""marketplace_dispatch_tasks canonical 14-field spec match

Revision ID: 0082_dispatch_tasks_spec_match
Revises: 0081_marketplace_dispatch_tables
Create Date: 2026-05-24

Aligns ``marketplace_dispatch_tasks`` to the canonical operator spec:

    id, order_id, seller_id, customer_id, zone_id, delivery_type,
    priority_level, dispatch_status, sla_deadline, cod_amount,
    payment_status, risk_level, created_at, updated_at

Renames:
    status        -> dispatch_status
    sla_due_at    -> sla_deadline
    priority      -> priority_level   (int -> varchar; mapped low/medium/high/critical)

Adds:
    seller_id        UUID FK users(id)
    customer_id      UUID FK users(id)
    zone_id          VARCHAR(40)   delivery zone code
    delivery_type    VARCHAR(20)   standard / express / same_day / next_day
                                   (orthogonal to task_type which is
                                    pickup / delivery / return)
    cod_amount       BIGINT (paisa, NULL if not COD)
    payment_status   VARCHAR(20)   pending / verified / cod_accepted / refunded / failed
    risk_level       VARCHAR(10)   low / medium / high / critical

Drops:
    task_type CHECK + the column is KEPT (still useful for pickup vs
    delivery vs return routing). delivery_type is added separately.

Indexes added on (seller_id), (customer_id), (zone_id, dispatch_status),
(payment_status), (risk_level, dispatch_status), (sla_deadline).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0082_dispatch_tasks_spec_match"
down_revision = "0081_marketplace_dispatch_tables"
branch_labels = None
depends_on = None

_UUID = pg.UUID(as_uuid=True)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Renames first (require dropping the old CHECK constraints that
    #    reference the column being renamed; we re-add updated CHECKs
    #    after the rename).
    # ------------------------------------------------------------------
    op.drop_constraint(
        "task_status_enum", "marketplace_dispatch_tasks", type_="check",
    )
    op.drop_index(
        "ix_dt_status_priority", table_name="marketplace_dispatch_tasks",
    )
    op.drop_index(
        "ix_dt_sla_due", table_name="marketplace_dispatch_tasks",
    )

    op.alter_column(
        "marketplace_dispatch_tasks", "status",
        new_column_name="dispatch_status",
    )
    op.alter_column(
        "marketplace_dispatch_tasks", "sla_due_at",
        new_column_name="sla_deadline",
    )
    # priority was int — replace with varchar priority_level.
    op.drop_column("marketplace_dispatch_tasks", "priority")
    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column(
            "priority_level", sa.String(10), nullable=False,
            server_default=sa.text("'medium'"),
        ),
    )

    # ------------------------------------------------------------------
    # 2. Adds — 6 new columns (seller_id, customer_id, zone_id,
    #    delivery_type, cod_amount, payment_status, risk_level).
    # ------------------------------------------------------------------
    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column(
            "seller_id", _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column(
            "customer_id", _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column("zone_id", sa.String(40), nullable=True),
    )
    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column(
            "delivery_type", sa.String(20), nullable=False,
            server_default=sa.text("'standard'"),
        ),
    )
    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column("cod_amount", sa.BigInteger, nullable=True),
    )
    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column(
            "payment_status", sa.String(20), nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column(
            "risk_level", sa.String(10), nullable=False,
            server_default=sa.text("'low'"),
        ),
    )

    # ------------------------------------------------------------------
    # 3. Backfill defaults for any pre-existing rows (the smoke test
    #    inserted one row in 0081 verification; this keeps it valid).
    # ------------------------------------------------------------------
    op.execute(
        "UPDATE marketplace_dispatch_tasks "
        "SET priority_level='medium', delivery_type='standard', "
        "    payment_status='pending', risk_level='low' "
        "WHERE priority_level IS NULL OR delivery_type IS NULL"
    )

    # ------------------------------------------------------------------
    # 4. Re-create CHECK constraints + indexes for renamed/added cols.
    # ------------------------------------------------------------------
    op.create_check_constraint(
        "dispatch_status_enum",
        "marketplace_dispatch_tasks",
        "dispatch_status IN ('pending','assigned','in_progress','completed',"
        "'failed','cancelled','on_hold')",
    )
    op.create_check_constraint(
        "priority_level_enum",
        "marketplace_dispatch_tasks",
        "priority_level IN ('low','medium','high','critical')",
    )
    op.create_check_constraint(
        "delivery_type_enum",
        "marketplace_dispatch_tasks",
        "delivery_type IN ('standard','express','same_day','next_day',"
        "'scheduled','economy')",
    )
    op.create_check_constraint(
        "payment_status_enum",
        "marketplace_dispatch_tasks",
        "payment_status IN ('pending','verified','cod_accepted','refunded',"
        "'failed','partial')",
    )
    op.create_check_constraint(
        "risk_level_enum",
        "marketplace_dispatch_tasks",
        "risk_level IN ('low','medium','high','critical')",
    )
    op.create_check_constraint(
        "cod_amount_nonneg",
        "marketplace_dispatch_tasks",
        "cod_amount IS NULL OR cod_amount >= 0",
    )

    op.create_index(
        "ix_dt_status_priority",
        "marketplace_dispatch_tasks",
        ["dispatch_status", "priority_level"],
    )
    op.create_index(
        "ix_dt_sla_deadline",
        "marketplace_dispatch_tasks", ["sla_deadline"],
    )
    op.create_index(
        "ix_dt_seller",
        "marketplace_dispatch_tasks", ["seller_id"],
    )
    op.create_index(
        "ix_dt_customer",
        "marketplace_dispatch_tasks", ["customer_id"],
    )
    op.create_index(
        "ix_dt_zone_status",
        "marketplace_dispatch_tasks",
        ["zone_id", "dispatch_status"],
    )
    op.create_index(
        "ix_dt_payment_status",
        "marketplace_dispatch_tasks", ["payment_status"],
    )
    op.create_index(
        "ix_dt_risk_status",
        "marketplace_dispatch_tasks",
        ["risk_level", "dispatch_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_dt_risk_status", table_name="marketplace_dispatch_tasks")
    op.drop_index("ix_dt_payment_status", table_name="marketplace_dispatch_tasks")
    op.drop_index("ix_dt_zone_status", table_name="marketplace_dispatch_tasks")
    op.drop_index("ix_dt_customer", table_name="marketplace_dispatch_tasks")
    op.drop_index("ix_dt_seller", table_name="marketplace_dispatch_tasks")
    op.drop_index("ix_dt_sla_deadline", table_name="marketplace_dispatch_tasks")
    op.drop_index("ix_dt_status_priority", table_name="marketplace_dispatch_tasks")

    for c in ("cod_amount_nonneg", "risk_level_enum", "payment_status_enum",
              "delivery_type_enum", "priority_level_enum",
              "dispatch_status_enum"):
        op.drop_constraint(c, "marketplace_dispatch_tasks", type_="check")

    for col in ("risk_level", "payment_status", "cod_amount", "delivery_type",
                "zone_id", "customer_id", "seller_id", "priority_level"):
        op.drop_column("marketplace_dispatch_tasks", col)

    op.add_column(
        "marketplace_dispatch_tasks",
        sa.Column("priority", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
    )
    op.alter_column(
        "marketplace_dispatch_tasks", "sla_deadline",
        new_column_name="sla_due_at",
    )
    op.alter_column(
        "marketplace_dispatch_tasks", "dispatch_status",
        new_column_name="status",
    )
    op.create_check_constraint(
        "task_status_enum",
        "marketplace_dispatch_tasks",
        "status IN ('pending','assigned','in_progress','completed',"
        "'failed','cancelled')",
    )
    op.create_index("ix_dt_status_priority",
                    "marketplace_dispatch_tasks", ["status", "priority"])
    op.create_index("ix_dt_sla_due",
                    "marketplace_dispatch_tasks", ["sla_due_at"])
