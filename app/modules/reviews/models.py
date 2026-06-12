"""ORM models for the reviews module — phase 1.

Three tables:

- ``product_reviews`` — one row per (customer, product). The
  ``order_id`` column anchors the verified-purchase claim so a
  later refund / chargeback can revisit eligibility. ``status``
  drives the moderation flow; the DB CHECK constraint mirrors
  ``codes.ALL_STATUSES`` so a buggy service write fails loudly.
- ``review_helpful_votes`` — append-only (review_id, customer_id)
  primary key makes upvotes idempotent without service-side dedupe.
- ``product_review_aggregates`` — denormalised average rating +
  review count per product. Recomputed on every status transition
  via service-level SQL (no triggers — keeps the recompute
  observable in audit logs and easy to test). Stored separately
  from ``products`` to avoid coupling the catalog table to a
  feature that's still finding its shape.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin
from app.modules.reviews.codes import (
    ALL_MEDIA_KINDS,
    ALL_STATUSES,
    STATUS_PENDING,
)


_STATUS_CHECK = "status IN (" + ",".join(f"'{s}'" for s in ALL_STATUSES) + ")"


class ProductReview(Base, TimestampMixin):
    __tablename__ = "product_reviews"

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
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The order that made this review verified-purchase eligible.
    # Kept for forensic / chargeback flows; NOT used for display.
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(length=160), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(length=16),
        nullable=False,
        server_default=STATUS_PENDING,
    )
    helpful_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(length=300), nullable=True,
    )
    moderated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    moderated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_rating_range"),
        CheckConstraint(_STATUS_CHECK, name="ck_review_status"),
        # One review per (customer, product) for phase 1. A later
        # phase may relax this for re-purchase scenarios.
        UniqueConstraint(
            "customer_id", "product_id", name="uq_review_customer_product",
        ),
        Index("ix_reviews_product_status", "product_id", "status"),
        Index("ix_reviews_customer", "customer_id"),
        # Sort by helpful_count DESC, created_at DESC for the public
        # listing — supporting index keeps the query off seq scans
        # once the table grows.
        Index(
            "ix_reviews_product_helpful",
            "product_id", "helpful_count", "created_at",
        ),
    )


class ReviewHelpfulVote(Base):
    """Idempotent upvote — one per (review, customer)."""

    __tablename__ = "review_helpful_votes"

    review_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_reviews.id", ondelete="CASCADE"),
        primary_key=True,
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    voted_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_helpful_votes_review", "review_id"),
    )


class ReviewMedia(Base, TimestampMixin):
    """Photo attached to a review — phase 2.

    One row per uploaded image. ``url`` is the resolved CDN/disk URL
    the frontend can render directly. ``object_key`` is the storage
    path (R2 key or disk-relative path) the admin tooling can use to
    delete on rejection in a future phase.

    Photos are visible to the public only when the parent
    ``ProductReview`` has ``status == approved`` — the public list
    query already filters to approved rows, so an unapproved photo
    is never served on the read path even if its URL is leaked.
    """

    __tablename__ = "review_media"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    review_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(length=16), nullable=False,
        server_default="image",
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ",".join(f"'{k}'" for k in ALL_MEDIA_KINDS) + ")",
            name="ck_review_media_kind",
        ),
        CheckConstraint(
            "file_size_bytes > 0", name="ck_review_media_size_positive",
        ),
        Index("ix_review_media_review", "review_id"),
    )


class ProductReviewAggregate(Base, TimestampMixin):
    """Denormalised review stats — recomputed on every status transition."""

    __tablename__ = "product_review_aggregates"

    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # avg_rating uses 3 digits / 2 decimals so 5.00 fits cleanly while
    # 1.00–5.00 is the practical range.
    avg_rating: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.00"),
    )
    review_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )

    __table_args__ = (
        CheckConstraint(
            "avg_rating BETWEEN 0 AND 5",
            name="ck_aggregate_avg_rating_range",
        ),
        CheckConstraint(
            "review_count >= 0", name="ck_aggregate_review_count_nonneg",
        ),
    )
