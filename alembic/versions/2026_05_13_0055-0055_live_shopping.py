"""0055 live_shopping — Module 50: Live Shopping MVP scaffold.

3 tables:
- live_streams      — one row per scheduled / live / ended broadcast
- stream_products   — products featured in a stream (many-to-many)
- stream_views      — append-only viewer log (one row per join event)

Streaming infrastructure (RTMP ingest, HLS playback, WebRTC) is
explicitly out of scope here — this models the metadata + commerce
overlay that surrounds a stream hosted on YouTube Live / Facebook Live /
Bunny Stream / a managed CDN. ``stream_url`` is the HLS playback URL
the storefront video player consumes.

State machine:
  scheduled → live → ended (TERMINAL)
            ↘ cancelled (TERMINAL)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0055_live_shopping"
down_revision: str | Sequence[str] | None = "0054_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_streams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("host_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("seller_id", UUID(as_uuid=True),
                  sa.ForeignKey("sellers.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("thumbnail_url", sa.Text, nullable=True),
        sa.Column("stream_url", sa.Text, nullable=True),
        sa.Column("provider", sa.String(40),
                  nullable=False, server_default="manual"),
        sa.Column("provider_stream_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(24),
                  nullable=False, server_default="scheduled"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("peak_viewers", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_views", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('scheduled','live','ended','cancelled')",
            name="ck_live_streams_status",
        ),
        sa.CheckConstraint(
            "provider IN ('manual','bunny','youtube','facebook','tiktok','custom_rtmp')",
            name="ck_live_streams_provider",
        ),
    )
    op.create_index(
        "ix_live_streams_status_scheduled",
        "live_streams", ["status", "scheduled_at"],
    )

    op.create_table(
        "stream_products",
        sa.Column("stream_id", UUID(as_uuid=True),
                  sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("product_id", UUID(as_uuid=True),
                  sa.ForeignKey("products.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("special_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("highlight_text", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_stream_products_stream_order",
        "stream_products", ["stream_id", "display_order"],
    )

    op.create_table(
        "stream_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("stream_id", UUID(as_uuid=True),
                  sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("viewer_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seconds_watched", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_stream_views_stream_joined",
        "stream_views", ["stream_id", "joined_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_stream_views_stream_joined", table_name="stream_views")
    op.drop_table("stream_views")
    op.drop_index("ix_stream_products_stream_order", table_name="stream_products")
    op.drop_table("stream_products")
    op.drop_index("ix_live_streams_status_scheduled", table_name="live_streams")
    op.drop_table("live_streams")
