"""0065 dashboard_layouts — per-user admin dashboard customization.

One row per user that has customised their dashboard. Absent row =
caller sees the default (registry order + everything visible). The
layout JSONB holds per-widget overrides:

  {
    "<widget_key>": {
      "hidden": false,
      "order": 50            // optional integer; lower = higher in group
    },
    ...
  }

Unknown keys (widgets not in the registry) are ignored on render.
Unknown overrides for a known widget fall back to the registry's
declared `order` and visible=true. Keeps the layout document tolerant
of widget additions + deletions.

Single-row-per-user — PK is user_id. Upsert via INSERT … ON CONFLICT.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID


revision: str = "0065_dashboard_layouts"
down_revision: str | Sequence[str] | None = "0064_module_config"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "dashboard_layouts",
        sa.Column(
            "user_id", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "layout", JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )


def downgrade() -> None:
    op.drop_table("dashboard_layouts")
