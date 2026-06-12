"""rider COD recharge sessions (Track A — Bkash Tokenized Checkout)

Revision ID: 0029_rider_cod_recharge
Revises: 0028_seo
Create Date: 2026-05-06

Adds the ``rider_cod_recharge_sessions`` table that tracks an in-app
Bkash payment from initiate → capture, linking back to a verified
``rider_settlements`` row on completion.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0029_rider_cod_recharge"
down_revision: str | Sequence[str] | None = "0028_seo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rider_cod_recharge_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "rider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rider_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "shift_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rider_shifts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requested_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "captured_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "mfs_provider",
            sa.String(length=16),
            nullable=False,
            server_default="bkash",
        ),
        sa.Column("payer_mfs_number", sa.String(length=32), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
        sa.Column("provider_trx_id", sa.String(length=64), nullable=True),
        sa.Column("bkash_url", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="initiated",
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "mfs_provider IN ('bkash')",
            name="ck_rider_cod_recharge_mfs_provider",
        ),
        sa.CheckConstraint(
            "status IN ('initiated','in_progress','completed','failed',"
            "'cancelled','expired')",
            name="ck_rider_cod_recharge_status",
        ),
        sa.CheckConstraint(
            "requested_amount > 0",
            name="ck_rider_cod_recharge_amount_positive",
        ),
    )
    op.create_index(
        "ix_rider_cod_recharge_rider_id",
        "rider_cod_recharge_sessions",
        ["rider_id"],
    )
    op.create_index(
        "ix_rider_cod_recharge_idempotency_key",
        "rider_cod_recharge_sessions",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_rider_cod_recharge_rider_status",
        "rider_cod_recharge_sessions",
        ["rider_id", "status"],
    )
    op.create_index(
        "ix_rider_cod_recharge_provider_payment_id",
        "rider_cod_recharge_sessions",
        ["provider_payment_id"],
        unique=True,
        postgresql_where=sa.text("provider_payment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rider_cod_recharge_provider_payment_id",
        table_name="rider_cod_recharge_sessions",
    )
    op.drop_index(
        "ix_rider_cod_recharge_rider_status",
        table_name="rider_cod_recharge_sessions",
    )
    op.drop_index(
        "ix_rider_cod_recharge_idempotency_key",
        table_name="rider_cod_recharge_sessions",
    )
    op.drop_index(
        "ix_rider_cod_recharge_rider_id",
        table_name="rider_cod_recharge_sessions",
    )
    op.drop_table("rider_cod_recharge_sessions")
