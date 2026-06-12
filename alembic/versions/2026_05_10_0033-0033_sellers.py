"""sellers module — phase 1 (data foundation + Hypershop Direct seed)

Revision ID: 0033_sellers
Revises: 0032_reviews
Create Date: 2026-05-10

Phase 1 of the multi-seller initiative. Creates two tables and seeds
a single ``Hypershop Direct`` seller representing first-party sales
so phase 2 (product ownership) can backfill existing products
against it without an extra setup step.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0033_sellers"
down_revision: str | Sequence[str] | None = "0032_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUSES = (
    "registered", "kyc_submitted", "approved", "rejected", "suspended",
)
_CADENCES = ("weekly", "biweekly", "monthly")
_METHODS = ("bkash", "bank_transfer", "nagad")
_ROLES = ("owner", "manager", "staff")


def _check(vals: tuple[str, ...], col: str) -> str:
    return f"{col} IN (" + ",".join(f"'{v}'" for v in vals) + ")"


def upgrade() -> None:
    op.create_table(
        "sellers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("business_name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False, unique=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="registered",
        ),
        sa.Column("tin", sa.String(length=32), nullable=True),
        sa.Column("nid", sa.String(length=32), nullable=True),
        sa.Column("trade_license_no", sa.String(length=64), nullable=True),
        sa.Column("bank_account_name", sa.String(length=200), nullable=True),
        sa.Column("bank_account_number", sa.String(length=32), nullable=True),
        sa.Column("bank_name", sa.String(length=120), nullable=True),
        sa.Column("bank_branch", sa.String(length=120), nullable=True),
        sa.Column(
            "commission_percent",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("10.00"),
        ),
        sa.Column(
            "payout_cadence",
            sa.String(length=16),
            nullable=False,
            server_default="monthly",
        ),
        sa.Column(
            "payout_method",
            sa.String(length=24),
            nullable=False,
            server_default="bkash",
        ),
        sa.Column("payout_account_id", sa.String(length=64), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "moderated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(_check(_STATUSES, "status"), name="ck_seller_status"),
        sa.CheckConstraint(
            _check(_CADENCES, "payout_cadence"),
            name="ck_seller_payout_cadence",
        ),
        sa.CheckConstraint(
            _check(_METHODS, "payout_method"),
            name="ck_seller_payout_method",
        ),
        sa.CheckConstraint(
            "commission_percent BETWEEN 0 AND 100",
            name="ck_seller_commission_range",
        ),
    )
    op.create_index("ix_sellers_status", "sellers", ["status"])

    op.create_table(
        "seller_users",
        sa.Column(
            "seller_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
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
            _check(_ROLES, "role"), name="ck_seller_user_role",
        ),
    )
    op.create_index(
        "uq_seller_user_user", "seller_users", ["user_id"], unique=True,
    )
    op.create_index(
        "ix_seller_user_seller", "seller_users", ["seller_id"],
    )

    # Seed the Hypershop Direct first-party seller so phase 2 has a
    # canonical row to backfill existing catalog products against.
    # Status starts as ``approved`` because first-party sales don't
    # require KYC review.
    op.execute(
        """
        INSERT INTO sellers (
            business_name, slug, status, commission_percent,
            payout_cadence, payout_method
        ) VALUES (
            'Hypershop Direct', 'hypershop-direct', 'approved', 0.00,
            'monthly', 'bank_transfer'
        )
        ON CONFLICT (slug) DO NOTHING
        """,
    )


def downgrade() -> None:
    op.drop_index("ix_seller_user_seller", table_name="seller_users")
    op.drop_index("uq_seller_user_user", table_name="seller_users")
    op.drop_table("seller_users")
    op.drop_index("ix_sellers_status", table_name="sellers")
    op.drop_table("sellers")
