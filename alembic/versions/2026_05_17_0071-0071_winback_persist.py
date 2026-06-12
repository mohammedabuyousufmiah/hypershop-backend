"""0071 cart_recovery winback persistence — drop NOT NULL on
``cart_id`` of ``hypershop_cart_recovery_dispatches`` and replace the
``(cart_id, milestone)`` UNIQUE constraint with a richer composite
UNIQUE that uses the customer key for winback rows.

Why: M3.A's strict NOT NULL on cart_id blocks win-back dispatch rows
(those have no cart). Without persisting them the recovery rate chart
cannot show winback throughput + recovery attribution.

Revision ID: 0071_winback_persist
Revises:    0070_cart_recovery
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0071_winback_persist"
down_revision = "0070_cart_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Loosen the cart_id FK so win-back rows (no cart) can persist.
    op.alter_column(
        "hypershop_cart_recovery_dispatches",
        "cart_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # The old UNIQUE (cart_id, milestone) becomes pointless for winback
    # rows where cart_id is NULL — Postgres treats NULL != NULL so it
    # would allow infinite winback duplicates. Replace it with two
    # partial-unique indexes:
    #   1. cart-flavoured: UNIQUE (cart_id, milestone) WHERE cart_id IS NOT NULL
    #   2. winback-flavoured: UNIQUE (customer_user_id, milestone)
    #      WHERE cart_id IS NULL AND customer_user_id IS NOT NULL
    op.drop_constraint(
        "uq_cart_recovery_dispatch_cart_milestone",
        "hypershop_cart_recovery_dispatches",
        type_="unique",
    )
    op.create_index(
        "ux_cart_recovery_dispatches_cart_milestone",
        "hypershop_cart_recovery_dispatches",
        ["cart_id", "milestone"],
        unique=True,
        postgresql_where=sa.text("cart_id IS NOT NULL"),
    )
    op.create_index(
        "ux_cart_recovery_dispatches_winback_customer_milestone",
        "hypershop_cart_recovery_dispatches",
        ["customer_user_id", "milestone"],
        unique=True,
        postgresql_where=sa.text(
            "cart_id IS NULL AND customer_user_id IS NOT NULL",
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_cart_recovery_dispatches_winback_customer_milestone",
        table_name="hypershop_cart_recovery_dispatches",
    )
    op.drop_index(
        "ux_cart_recovery_dispatches_cart_milestone",
        table_name="hypershop_cart_recovery_dispatches",
    )
    op.create_unique_constraint(
        "uq_cart_recovery_dispatch_cart_milestone",
        "hypershop_cart_recovery_dispatches",
        ["cart_id", "milestone"],
    )
    op.alter_column(
        "hypershop_cart_recovery_dispatches",
        "cart_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
