"""Customer credit wallet (ledger)

Revision ID: 0009_customer_wallet
Revises: 0007_delivery
Create Date: 2026-05-03

The append-only customer credit wallet. ``wallet_transactions`` is REVOKEd
UPDATE/DELETE from PUBLIC so a service bug cannot rewrite credit history.
``wallet_credits`` is intentionally mutable (remaining_amount changes on
debit/expire).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_customer_wallet"
down_revision: str | Sequence[str] | None = "0007_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- customer_wallets ----------------
    op.create_table(
        "customer_wallets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "customer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
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
        sa.UniqueConstraint("customer_user_id", name="uq_customer_wallets_user"),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_customer_wallets_currency_iso",
        ),
    )

    # ---------------- wallet_credits ----------------
    op.create_table(
        "wallet_credits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("remaining_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active",
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
        sa.Column("expires_at", sa.Date(), nullable=False),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("amount > 0", name="ck_wallet_credits_amount_positive"),
        sa.CheckConstraint(
            "remaining_amount >= 0 AND remaining_amount <= amount",
            name="ck_wallet_credits_remaining_in_bounds",
        ),
        sa.CheckConstraint(
            "status IN ('active','exhausted','expired','rolled_over')",
            name="ck_wallet_credits_status_enum",
        ),
    )
    op.create_index(
        "ix_wallet_credits_wallet_status", "wallet_credits", ["wallet_id", "status"],
    )
    op.create_index(
        "ix_wallet_credits_expires_at", "wallet_credits", ["expires_at"],
    )

    # ---------------- wallet_transactions ----------------
    op.create_table(
        "wallet_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customer_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "credit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet_credits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(14, 2), nullable=False),
        sa.Column("source_type", sa.String(48), nullable=True),
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
        sa.Column("notes", sa.String(512), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "kind IN ('grant','redeem','expire','rollover','adjust')",
            name="ck_wallet_txn_kind_enum",
        ),
        sa.CheckConstraint("amount > 0", name="ck_wallet_txn_amount_positive"),
        sa.CheckConstraint(
            "balance_after >= 0", name="ck_wallet_txn_balance_nonneg",
        ),
    )
    op.create_index(
        "ix_wallet_txn_wallet_at", "wallet_transactions", ["wallet_id", "occurred_at"],
    )
    op.create_index(
        "ix_wallet_txn_credit_id", "wallet_transactions", ["credit_id"],
    )

    # Append-only ledger.
    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE wallet_transactions FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )


def downgrade() -> None:
    op.drop_index("ix_wallet_txn_credit_id", table_name="wallet_transactions")
    op.drop_index("ix_wallet_txn_wallet_at", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")

    op.drop_index("ix_wallet_credits_expires_at", table_name="wallet_credits")
    op.drop_index("ix_wallet_credits_wallet_status", table_name="wallet_credits")
    op.drop_table("wallet_credits")

    op.drop_table("customer_wallets")
