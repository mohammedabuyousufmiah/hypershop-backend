"""0043 gift_cards — pre-funded codes that credit loyalty balance on redeem.

One table: ``gift_cards`` with status enum + unique code. Redeem flow
bridges to loyalty via a single ADJUST transaction (face_value treated
as points 1:1).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


revision: str = "0043_gift_cards"
down_revision: str | Sequence[str] | None = "0042_wishlist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gift_cards",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("face_value_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default=sa.text("'BDT'"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "purchased_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "redeemed_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "redeemed_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('active','redeemed','expired','revoked')",
            name="ck_gift_cards_status",
        ),
        sa.CheckConstraint(
            "face_value_minor > 0", name="ck_gift_cards_face_value_pos",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_gift_cards_currency_iso",
        ),
        sa.CheckConstraint(
            "(status <> 'redeemed') OR "
            "(redeemed_by_user_id IS NOT NULL AND redeemed_at IS NOT NULL)",
            name="ck_gift_cards_redeemed_pair",
        ),
    )
    op.create_index("ix_gift_cards_status", "gift_cards", ["status"])
    op.create_index(
        "ix_gift_cards_redeemed_by", "gift_cards", ["redeemed_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_gift_cards_redeemed_by", table_name="gift_cards")
    op.drop_index("ix_gift_cards_status", table_name="gift_cards")
    op.drop_table("gift_cards")
