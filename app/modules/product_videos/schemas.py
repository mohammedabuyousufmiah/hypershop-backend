"""Pydantic schemas exchanged at the public + admin video API boundary."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------- public ----------


class PublicProductVideo(BaseModel):
    """Video shape returned to the customer-web product page.

    Only ``approved`` videos are ever serialised through this schema.
    All URLs are absolute and ready for the player. Notable fields
    deliberately ABSENT here:

    * ``raw_object_key`` — the private R2 key for the original upload.
      Only ``AdminProductVideo`` carries it; surfacing it publicly
      would let a customer pull the un-compressed original.
    * Direct CDN URLs to the raw or to anything under the R2 private
      prefix — same reason.
    * ``status`` — every row this schema serialises is ``approved`` by
      construction; the field would be informational noise.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    title: str | None
    hls_url: str
    thumbnail_url: str | None
    duration_seconds: int | None


class PublicProductVideoList(BaseModel):
    items: list[PublicProductVideo]


class ProductVideoUploadResponse(BaseModel):
    """Tight response for the seller-facing upload endpoint.

    The seller-app/admin-app only needs the new id (to poll status)
    plus the initial state — full details are fetched separately via
    ``GET /admin/catalog/videos/{id}`` once processing finishes.
    """

    video_id: UUID
    status: str


class VideoEventCreate(BaseModel):
    """Body for ``POST /catalog/videos/{video_id}/events``.

    Anonymous + heavily rate-limited. ``customer_id`` is set by the
    server from the authenticated principal when present; the client
    only supplies ``session_id`` + ``event_type`` + optional
    ``watch_seconds``.
    """

    event_type: str = Field(min_length=4, max_length=32)
    session_id: str | None = Field(default=None, max_length=64)
    watch_seconds: int = Field(default=0, ge=0, le=24 * 3600)


# ---------- admin ----------


class AdminProductVideo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    seller_id: UUID | None
    title: str | None
    status: str
    raw_object_key: str | None
    hls_url: str | None
    thumbnail_url: str | None
    duration_seconds: int | None
    file_size_bytes: int | None
    rejection_reason: str | None
    processing_error: str | None
    # Per-event timeline (nullable). ``updated_at`` still tracks the
    # most-recent change to ANY column; the three timestamps below
    # stick to the specific transition that set them so admin sorts
    # don't get reshuffled by unrelated edits.
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    disabled_at: datetime | None
    reopened_at: datetime | None


class AdminVideoList(BaseModel):
    items: list[AdminProductVideo]
    total: int


class AdminVideoApprove(BaseModel):
    pass


class AdminVideoReject(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class AdminVideoReopen(BaseModel):
    """Body for ``POST /admin/product-videos/{id}/reopen``.

    The reason is mandatory — the audit log records *why* a previously
    rejected video is being given a second moderation pass. The old
    ``rejection_reason`` on the row is preserved (not overwritten) so
    the historical context survives the reopen.
    """

    reason: str = Field(min_length=3, max_length=2000)


class AdminVideoDisable(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class AdminVideoUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=160)
