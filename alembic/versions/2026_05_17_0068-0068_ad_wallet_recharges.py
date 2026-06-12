"""0068 ad_wallet_recharges — Phase 1.B persisted recharge table.

Replaces the ephemeral ``module_settings`` stash used in Phase 1.A with a
real, queryable table. One row per provider checkout session; uniqueness
on ``provider_payment_id`` is the contract that gives us safe webhook
re-delivery (idempotent credit).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

# revision identifiers, used by Alembic.
revision: str = "0068_ad_wallet_recharges"
down_revision: str | Sequence[str] | None = "0067_ads_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hypershop_ad_wallet_recharges",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "ad_wallet_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_ad_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "seller_id",
            PgUUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_payment_id", sa.String(128), nullable=True),
        sa.Column("provider_transaction_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.String(256), nullable=True),
        sa.Column(
            "wallet_txn_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_ad_wallet_txns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider_response", JSONB(), nullable=True),
        sa.Column("bkash_create_url", sa.Text(), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "amount_minor >= 10000",
            name="ck_hypershop_ad_wallet_recharges_min_amount",
        ),
        sa.CheckConstraint(
            "provider IN ('bkash','sslcommerz','fake')",
            name="ck_hypershop_ad_wallet_recharges_provider",
        ),
        sa.CheckConstraint(
            "status IN ('initiated','pending','succeeded','failed','expired','refunded')",
            name="ck_hypershop_ad_wallet_recharges_status",
        ),
        sa.UniqueConstraint(
            "provider_payment_id",
            name="uq_hypershop_ad_wallet_recharges_provider_payment_id",
        ),
    )

    op.create_index(
        "ix_hypershop_ad_wallet_recharges_seller_at",
        "hypershop_ad_wallet_recharges",
        ["seller_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_hypershop_ad_wallet_recharges_status_at",
        "hypershop_ad_wallet_recharges",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('initiated','pending')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hypershop_ad_wallet_recharges_status_at",
        table_name="hypershop_ad_wallet_recharges",
    )
    op.drop_index(
        "ix_hypershop_ad_wallet_recharges_seller_at",
        table_name="hypershop_ad_wallet_recharges",
    )
    op.drop_table("hypershop_ad_wallet_recharges")
