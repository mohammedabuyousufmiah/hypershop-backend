"""product_videos module — short product videos with FFmpeg + admin approval

Revision ID: 0030_product_videos
Revises: 0029_rider_cod_recharge
Create Date: 2026-05-10

Adds two tables that let every product surface up-to-5 approved
short videos:

- ``product_videos`` — uploads + processing state + HLS output URLs +
  optional ``seller_id`` for multi-seller marketplaces.
- ``video_events`` — append-only telemetry. Six event types covering
  basic playback (impression / play / pause / complete) plus
  conversion attribution events fired after a video has been viewed
  (add_to_cart_after_video, buy_now_after_video).

Both tables hang off ``products`` via FK with ``ON DELETE CASCADE``
so the lifecycle is owned by the catalog row.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0030_product_videos"
down_revision: str | Sequence[str] | None = "0029_rider_cod_recharge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_videos",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "seller_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("raw_object_key", sa.Text(), nullable=True),
        sa.Column("hls_url", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("processing_error", sa.Text(), nullable=True),
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
            "status IN ('uploaded','processing','ready_for_review',"
            "'approved','rejected','disabled','failed')",
            name="ck_product_videos_status_enum",
        ),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes > 0",
            name="ck_product_videos_file_size_positive",
        ),
    )
    op.create_index(
        "ix_product_videos_product_id",
        "product_videos",
        ["product_id"],
    )
    op.create_index(
        "ix_product_videos_product_status",
        "product_videos",
        ["product_id", "status"],
    )
    op.create_index(
        "ix_product_videos_status",
        "product_videos",
        ["status"],
    )
    op.create_index(
        "ix_product_videos_seller_id",
        "product_videos",
        ["seller_id"],
        postgresql_where=sa.text("seller_id IS NOT NULL"),
    )

    op.create_table(
        "video_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "video_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column(
            "watch_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "event_type IN ('impression','play','pause','complete',"
            "'add_to_cart_after_video','buy_now_after_video')",
            name="ck_video_events_event_type_enum",
        ),
        sa.CheckConstraint(
            "watch_seconds >= 0",
            name="ck_video_events_watch_seconds_nonneg",
        ),
    )
    op.create_index(
        "ix_video_events_video_id",
        "video_events",
        ["video_id"],
    )
    op.create_index(
        "ix_video_events_product_id",
        "video_events",
        ["product_id"],
    )
    op.create_index(
        "ix_video_events_customer_id",
        "video_events",
        ["customer_id"],
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )
    op.create_index(
        "ix_video_events_video_session_event",
        "video_events",
        ["video_id", "session_id", "event_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_video_events_video_session_event",
        table_name="video_events",
    )
    op.drop_index(
        "ix_video_events_customer_id",
        table_name="video_events",
    )
    op.drop_index(
        "ix_video_events_product_id",
        table_name="video_events",
    )
    op.drop_index(
        "ix_video_events_video_id",
        table_name="video_events",
    )
    op.drop_table("video_events")

    op.drop_index(
        "ix_product_videos_seller_id", table_name="product_videos",
    )
    op.drop_index("ix_product_videos_status", table_name="product_videos")
    op.drop_index(
        "ix_product_videos_product_status", table_name="product_videos",
    )
    op.drop_index(
        "ix_product_videos_product_id", table_name="product_videos",
    )
    op.drop_table("product_videos")
