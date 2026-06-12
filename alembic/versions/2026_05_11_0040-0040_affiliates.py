"""0040 affiliates — share-and-earn per-product attribution.

One table:
  * ``affiliate_records`` — one row per customer user.
    ``code`` is a short token used in the ``?aff=`` cookie (30-day TTL
    set by the storefront on click). ``commission_rate`` is the
    fraction-of-subtotal commission paid to the affiliate when an
    order they sourced reaches ``payment_confirmed``.

Why no separate "conversions" table: every affiliated order carries a
loyalty ADJUST row referencing the order_id, plus the running
``total_conversions`` + ``total_commission_minor`` counters on the
record. Idempotency is provided by the LoyaltyService.adjust()
de-dup on the reason string containing the order_id.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


revision: str = "0040_affiliates"
down_revision: str | Sequence[str] | None = "0039_referrals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "affiliate_records",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column(
            "commission_rate",
            sa.Numeric(5, 4),
            nullable=False,
            server_default=sa.text("0.05"),  # 5 %
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "total_conversions",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_commission_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 1",
            name="ck_affiliate_commission_rate_range",
        ),
    )
    op.create_index(
        "ix_affiliate_records_lower",
        "affiliate_records",
        [sa.text("lower(code)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_affiliate_records_lower", table_name="affiliate_records")
    op.drop_table("affiliate_records")
