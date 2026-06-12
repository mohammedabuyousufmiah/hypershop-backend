"""0044 user_pins — customer-self PIN for quick re-auth (M32).

One table:
  * ``user_pins`` — PK on user_id (one row per user). Stores a bcrypt
    hash; raw PINs never touch disk. Failed-attempt counter + lockout
    timestamp track brute-force defence.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


revision: str = "0044_user_pins"
down_revision: str | Sequence[str] | None = "0043_gift_cards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_pins",
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("pin_hash", sa.String(255), nullable=False),
        sa.Column(
            "failed_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "locked_until", sa.DateTime(timezone=True), nullable=True,
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
        sa.CheckConstraint(
            "failed_attempts >= 0", name="ck_user_pins_attempts_nonneg",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_pins")
