"""0039 referrals — invite codes + attribution + first-order reward.

Two tables:
  * ``referral_codes`` — one row per customer. ``code`` is a short
    case-insensitive token (8 chars) generated at first request. Index
    on lower(code) for the attribution lookup.
  * ``referral_attributions`` — one row when a NEW signup pastes a
    referrer's code (or hits ``?ref=CODE`` link). The referee_user_id
    is unique, so each new customer can be attributed to at most one
    referrer. ``status`` ratchets: PENDING → REWARDED (on first order
    PAID) or EXPIRED (admin-driven, e.g. fraud reversal).

Reward policy: on the referee's first order reaching ``payment_confirmed``,
the referrer receives a 100-point loyalty credit (and the referee gets a
50-point welcome credit). The referee themselves do NOT receive points
for being attributed — only for actually buying. This stops free-account
abuse.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


revision: str = "0039_referrals"
down_revision: str | Sequence[str] | None = "0038_loyalty"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "referral_codes",
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
            "total_referrals",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rewarded_referrals",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_referral_codes_lower",
        "referral_codes",
        [sa.text("lower(code)")],
        unique=True,
    )

    op.create_table(
        "referral_attributions",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("referrer_user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "referee_user_id",
            pg.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "rewarded_by_order_id", pg.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','REWARDED','EXPIRED')",
            name="ck_referral_attribution_status",
        ),
    )
    op.create_index(
        "ix_referral_attr_referrer",
        "referral_attributions",
        ["referrer_user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_referral_attr_referrer", table_name="referral_attributions"
    )
    op.drop_table("referral_attributions")
    op.drop_index("ix_referral_codes_lower", table_name="referral_codes")
    op.drop_table("referral_codes")
