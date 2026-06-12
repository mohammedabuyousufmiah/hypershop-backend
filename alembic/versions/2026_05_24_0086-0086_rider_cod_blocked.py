"""rider_capacity.cod_blocked flag

Revision ID: 0086_rider_cod_blocked
Revises: 0085_rider_cod_holdings
Create Date: 2026-05-24

Adds a boolean ``cod_blocked`` to ``marketplace_rider_capacity``.
When True, the assignment service refuses to assign any COD order to
the rider — regardless of their cap utilisation. Used as an operator
override (e.g. settlement pending, suspected COD mishandling, rider
under investigation) without touching the rider's normal max float.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0086_rider_cod_blocked"
down_revision = "0085_rider_cod_holdings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "marketplace_rider_capacity",
        sa.Column(
            "cod_blocked", sa.Boolean, nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_rc_cod_blocked",
        "marketplace_rider_capacity", ["cod_blocked"],
        postgresql_where=sa.text("cod_blocked = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_rc_cod_blocked", table_name="marketplace_rider_capacity")
    op.drop_column("marketplace_rider_capacity", "cod_blocked")
