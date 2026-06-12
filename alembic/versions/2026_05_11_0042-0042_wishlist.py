"""0042 wishlist — customer save-for-later (Daraz / Noon parity).

One table:
  * ``wishlist_items`` — composite-unique on (user_id, product_id). No
    quantity, no notes; the cart is the right place for both.

Rationale: separate table from carts because wishlist is long-lived
across sessions (not snapshotted at checkout) and we want to clear a
cart without losing wishlist state. Composite UNIQUE makes
"already in wishlist?" lookups O(log n) and lets the service insert
idempotently.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg


revision: str = "0042_wishlist"
down_revision: str | Sequence[str] | None = "0041_phase_b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wishlist_items",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "product_id", name="uq_wishlist_user_product",
        ),
    )
    op.create_index(
        "ix_wishlist_user_created",
        "wishlist_items",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_wishlist_user_created", table_name="wishlist_items")
    op.drop_table("wishlist_items")
