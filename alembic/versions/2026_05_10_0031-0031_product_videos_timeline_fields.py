"""product_videos timeline fields (approved_at, disabled_at, reopened_at)

Revision ID: 0031_product_videos_timeline
Revises: 0030_product_videos
Create Date: 2026-05-10

Splits the lifecycle timeline into per-event columns so customer
feeds (sort by ``approved_at DESC``) don't get reshuffled when an
admin edits a title or reenables a video — those bump
``updated_at`` but leave ``approved_at`` untouched.

Also adds an index on ``updated_at`` to back the new admin
"recently moderated" queue (``GET /admin/product-videos/
recently-moderated``).

The three new columns are NULLABLE and have no default — they're
populated by the service layer at the relevant transition:

  approve(...)  → SET approved_at = now() AT TIME ZONE 'UTC'
  disable(...)  → SET disabled_at = now() AT TIME ZONE 'UTC'
  reopen(...)   → SET reopened_at = now() AT TIME ZONE 'UTC'

There is NO data backfill in this migration: the module hasn't
shipped to production yet, so every existing row (in any dev/staging
DB) is OK to leave with the new columns NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031_product_videos_timeline"
down_revision: str | Sequence[str] | None = "0030_product_videos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_videos",
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "product_videos",
        sa.Column(
            "disabled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "product_videos",
        sa.Column(
            "reopened_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Powers the admin "recently moderated" feed. Postgres btree
    # indexes serve both ASC and DESC equally — no need for a DESC
    # specification at create time.
    op.create_index(
        "ix_product_videos_updated_at",
        "product_videos",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_videos_updated_at",
        table_name="product_videos",
    )
    op.drop_column("product_videos", "reopened_at")
    op.drop_column("product_videos", "disabled_at")
    op.drop_column("product_videos", "approved_at")
