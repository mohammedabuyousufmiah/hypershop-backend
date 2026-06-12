"""0056 funnel_segments — sprint 15: named behavioral cohort definitions.

Adds a single table `funnel_segments` that operators use to define
named behavioral segments (e.g. "Browsers - 7d", "Cart abandoners",
"Lapsed VIPs"). Rule schema is JSONB; the segmentation service in
`app/modules/funnel/segmentation.py` translates rules into SQL
against funnel_events + funnel_customers.

Rule schema example:
{
  "did_events":      ["product_view", "add_to_cart"],
  "did_not_events":  ["order_placed", "order_completed"],
  "in_last_days":    7,
  "min_event_count": 3,
  "score_min":       null,
  "score_max":       null,
  "category_id_in":  ["uuid-..."],
  "consent_marketing": true
}
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0056_funnel_segments"
down_revision: str | Sequence[str] | None = "0055_live_shopping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "funnel_segments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("rules", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("estimated_count", sa.Integer,
                  nullable=False, server_default="0"),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean,
                  nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("funnel_segments")
