"""marketplace_rider_cod_holdings — per-event COD exposure log

Revision ID: 0085_rider_cod_holdings
Revises: 0084_delivery_attempts_spec
Create Date: 2026-05-24

New per-event table capturing the rider's COD exposure snapshot at the
moment a COD order is assigned to them:

    id
    rider_id                       FK users
    order_id                       FK orders
    cod_amount                     this order's COD (paisa)
    current_cod_holding            rider's running total INCLUDING this
                                   order (paisa)
    cod_limit                      rider's COD cap AT THIS MOMENT (paisa,
                                   copied from rider_capacity at write
                                   time so the audit row is self-contained
                                   even if the cap is later changed)
    risk_status                    safe / watch / high / blocked
    blocked_from_cod_assignment    True when current_cod_holding +
                                   cod_amount would exceed cod_limit
                                   → assignment service rejects
    created_at

Differs from:
  * marketplace_cod_risk_logs    per-order risk SCORE (0..100) + factors
  * marketplace_rider_capacity   per-rider one-row snapshot

This table is append-only history of every COD assignment decision so
finance can reconstruct who was holding what when, and audit any cap
breach.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0085_rider_cod_holdings"
down_revision = "0084_delivery_attempts_spec"
branch_labels = None
depends_on = None

_UUID = pg.UUID(as_uuid=True)
_NOW_UTC = sa.text("(now() AT TIME ZONE 'UTC')")


def upgrade() -> None:
    op.create_table(
        "marketplace_rider_cod_holdings",
        sa.Column(
            "id", _UUID, primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", _UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "order_id", _UUID,
            sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("cod_amount", sa.BigInteger, nullable=False),
        sa.Column("current_cod_holding", sa.BigInteger, nullable=False),
        sa.Column("cod_limit", sa.BigInteger, nullable=False),
        sa.Column(
            "risk_status", sa.String(10), nullable=False,
            server_default=sa.text("'safe'"),
        ),
        sa.Column(
            "blocked_from_cod_assignment", sa.Boolean, nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=_NOW_UTC,
        ),
        sa.CheckConstraint(
            "risk_status IN ('safe','watch','high','blocked')",
            name="risk_status_enum",
        ),
        sa.CheckConstraint(
            "cod_amount >= 0 AND current_cod_holding >= 0 AND cod_limit >= 0",
            name="cod_amounts_nonneg",
        ),
    )
    op.create_index(
        "ix_rch_rider_time",
        "marketplace_rider_cod_holdings",
        ["rider_id", "created_at"],
    )
    op.create_index(
        "ix_rch_order",
        "marketplace_rider_cod_holdings", ["order_id"],
    )
    op.create_index(
        "ix_rch_blocked",
        "marketplace_rider_cod_holdings", ["blocked_from_cod_assignment"],
        postgresql_where=sa.text("blocked_from_cod_assignment = true"),
    )
    op.create_index(
        "ix_rch_risk",
        "marketplace_rider_cod_holdings", ["risk_status"],
    )

    # Defence-in-depth: append-only — REVOKE UPDATE/DELETE from PUBLIC.
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE marketplace_rider_cod_holdings "
        "FROM PUBLIC"
    )


def downgrade() -> None:
    op.drop_index("ix_rch_risk", table_name="marketplace_rider_cod_holdings")
    op.drop_index("ix_rch_blocked", table_name="marketplace_rider_cod_holdings")
    op.drop_index("ix_rch_order", table_name="marketplace_rider_cod_holdings")
    op.drop_index("ix_rch_rider_time", table_name="marketplace_rider_cod_holdings")
    op.drop_table("marketplace_rider_cod_holdings")
