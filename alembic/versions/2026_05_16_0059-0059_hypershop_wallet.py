"""0059 hypershop_wallet — customer-facing e-commerce wallet.

Two new tables, deliberately separate from the pharmacy
``customer_wallets`` / ``wallet_credits`` / ``wallet_transactions`` triple:

  hypershop_wallets       — one row per user; carries the running balance.
  hypershop_wallet_txns   — append-only transaction log (credit / debit /
                            adjust) with running balance snapshots.

Why a fresh table set:
  - The pharmacy wallet uses a credit-bucket model with mandatory
    expiry per top-up. E-commerce wallet wants a single rolling balance
    (refund credit, gift-card top-up, loyalty redemption all flow into
    one number).
  - Per the project's "no pharmacy in Hypershop" rule, the new module
    must not import from ``app.modules.doctor_rx``. Sharing the table
    schema would force the import; sharing the SQLAlchemy model would
    force a double-mapping.
  - Money stored as integer minor units (paisa, 1 BDT = 100). Balance
    column is BIGINT to safely hold lifetime accumulation.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

# revision identifiers, used by Alembic.
revision: str = "0059_hypershop_wallet"
down_revision: str | Sequence[str] | None = "0058_seo_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hypershop_wallets",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "customer_user_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default=sa.text("'BDT'"),
        ),
        sa.Column(
            "balance_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
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
        sa.UniqueConstraint("customer_user_id", name="uq_hypershop_wallets_user"),
        sa.CheckConstraint(
            "balance_minor >= 0",
            name="ck_hypershop_wallet_balance_nonneg",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'FROZEN', 'CLOSED')",
            name="ck_hypershop_wallet_status",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_hypershop_wallet_currency_iso",
        ),
    )
    op.create_table(
        "hypershop_wallet_txns",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "wallet_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        # Always a positive amount; sign is implied by `kind`.
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_minor", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=True),
        sa.Column("source_id", PgUUID(as_uuid=True), nullable=True),
        sa.Column("memo", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_hypershop_wallet_txn_amount_positive",
        ),
        sa.CheckConstraint(
            "balance_after_minor >= 0",
            name="ck_hypershop_wallet_txn_balance_nonneg",
        ),
        sa.CheckConstraint(
            "kind IN ('credit', 'debit', 'adjust')",
            name="ck_hypershop_wallet_txn_kind",
        ),
    )
    op.create_index(
        "ix_hypershop_wallet_txn_wallet_at",
        "hypershop_wallet_txns",
        ["wallet_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hypershop_wallet_txn_wallet_at",
        table_name="hypershop_wallet_txns",
    )
    op.drop_table("hypershop_wallet_txns")
    op.drop_table("hypershop_wallets")
