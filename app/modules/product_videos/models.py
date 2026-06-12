"""ORM models for the product_videos module.

Two tables:

- ``product_videos`` — one row per uploaded short product video.
  Lifecycle: ``uploaded`` → ``processing`` → ``ready_for_review`` →
  ``approved`` (public) / ``rejected`` (terminal). ``disabled`` is a
  soft-hide an admin can flip on/off without deleting the row;
  ``failed`` is the terminal state for an unrecoverable ffmpeg error.
- ``video_events`` — append-only telemetry. Six event types covering
  basic playback (impression / play / pause / complete) plus two
  conversion attribution events the product page fires after a video
  has been viewed (add-to-cart-after-video, buy-now-after-video).

Storage shape is "S3-ready" without committing to S3 today:

- ``raw_object_key`` is the path (or eventual S3 key) of the original
  upload. Today we write it as a path relative to
  ``settings.product_video_storage_dir``; when the operator switches
  to S3/MinIO they can populate the same column with the bucket key
  and swap the storage adapter — no schema change.
- ``hls_url`` and ``thumbnail_url`` are persisted as already-resolved
  absolute URLs (built via :func:`storage.public_url_for` at the end
  of FFmpeg processing) so the customer-web client can pass them
  straight to the HLS player without any base-URL gymnastics.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class ProductVideo(Base, TimestampMixin):
    __tablename__ = "product_videos"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional — set when a multi-seller marketplace flow is wired in.
    # No FK today because the project doesn't yet have a `sellers` table;
    # adding the FK later is a single ALTER TABLE.
    seller_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default="uploaded",
    )

    raw_object_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    hls_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text(), nullable=True)

    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    rejection_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text(), nullable=True)

    # Per-event timeline. Each is set ONLY by the service method that
    # performs the matching transition; ``updated_at`` (from
    # TimestampMixin) bumps on every UPDATE, but these stay sticky so
    # customer feeds + analytics don't get reshuffled by unrelated
    # admin actions like title edits.
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded','processing','ready_for_review',"
            "'approved','rejected','disabled','failed')",
            name="status_enum",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes > 0",
            name="file_size_positive",
        ),
        Index("ix_product_videos_product_id", "product_id"),
        Index(
            "ix_product_videos_product_status",
            "product_id",
            "status",
        ),
        Index("ix_product_videos_status", "status"),
        Index(
            "ix_product_videos_seller_id",
            "seller_id",
            postgresql_where=text("seller_id IS NOT NULL"),
        ),
    )


class VideoEvent(Base):
    __tablename__ = "video_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    video_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_videos.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Logged-in customer (when known). Null for anonymous views.
    customer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    # Anonymous client-generated session token. Allowed null so the
    # commerce-attribution events (ATC/Buy after video) can be fired
    # by server-side code paths that don't have one.
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    watch_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('impression','play','pause','complete',"
            "'add_to_cart_after_video','buy_now_after_video')",
            name="event_type_enum",
        ),
        CheckConstraint(
            "watch_seconds >= 0",
            name="watch_seconds_nonneg",
        ),
        Index("ix_video_events_video_id", "video_id"),
        Index("ix_video_events_product_id", "product_id"),
        Index(
            "ix_video_events_customer_id",
            "customer_id",
            postgresql_where=text("customer_id IS NOT NULL"),
        ),
        Index(
            "ix_video_events_video_session_event",
            "video_id",
            "session_id",
            "event_type",
        ),
    )
