"""Pydantic schemas for the reviews module — phase 1.

Wire shapes are deliberately small: customer create takes (rating,
title, body), customer read returns a redacted view (no email,
no internal status timestamps), admin read includes the full row.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.reviews.codes import BODY_MAX, RATING_MAX, RATING_MIN, TITLE_MAX


class ReviewCreateIn(BaseModel):
    rating: int = Field(..., ge=RATING_MIN, le=RATING_MAX)
    title: str | None = Field(None, max_length=TITLE_MAX)
    # ``min_length=10`` keeps drive-by "ok" reviews out of the corpus
    # without rejecting genuine short reviews. Tunable later if BD
    # customers report it as too strict.
    body: str = Field(..., min_length=10, max_length=BODY_MAX)


class ReviewUpdateIn(BaseModel):
    rating: int | None = Field(None, ge=RATING_MIN, le=RATING_MAX)
    title: str | None = Field(None, max_length=TITLE_MAX)
    body: str | None = Field(None, min_length=10, max_length=BODY_MAX)


class ReviewRejectIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


class PublicReviewOut(BaseModel):
    """Shape returned to anonymous + customer callers on list / get."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    rating: int
    title: str | None
    body: str
    helpful_count: int
    created_at: datetime
    # Display name derived in the serializer (NOT a column) — keeps
    # email / phone out of the public surface.
    customer_display_name: str | None = None
    is_verified_purchaser: bool = True  # phase 1 = always True


class AdminReviewOut(BaseModel):
    """Shape returned on the admin moderation queue."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    customer_id: UUID
    order_id: UUID | None
    rating: int
    title: str | None
    body: str
    status: str
    helpful_count: int
    rejection_reason: str | None
    moderated_by: UUID | None
    moderated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReviewListOut(BaseModel):
    items: list[PublicReviewOut]
    total: int


class AdminReviewListOut(BaseModel):
    items: list[AdminReviewOut]
    total: int


class ProductRatingOut(BaseModel):
    """Aggregate response — what the PDP rating widget consumes."""

    product_id: UUID
    avg_rating: Decimal
    review_count: int


class HelpfulVoteOut(BaseModel):
    review_id: UUID
    helpful_count: int
    voted: bool  # False = already voted; True = first-time vote


# ---------------- Phase-2 review media ----------------


class ReviewMediaOut(BaseModel):
    """Single attached photo on a review."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_id: UUID
    kind: str
    url: str
    content_type: str
    file_size_bytes: int
    position: int


class PublicReviewWithMediaOut(PublicReviewOut):
    """Public review shape that also surfaces attached media (phase 2)."""

    media: list[ReviewMediaOut] = []


class ReviewListWithMediaOut(BaseModel):
    items: list[PublicReviewWithMediaOut]
    total: int
