"""Shared helpers for the product_videos test suite.

These bypass the upload + worker pipeline by writing rows directly so
visibility / moderation tests don't need ffmpeg, R2, or Bunny set up.
The upload-flow tests (in ``test_product_videos_admin.py``) DO go
through the real router so the validation + storage-spool path is
covered end-to-end.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.db.session import get_sessionmaker
from app.core.time import utc_now
from app.modules.catalog.models import Product, ProductStatus
from app.modules.product_videos.models import ProductVideo, VideoEvent


async def seed_product(*, slug: str | None = None) -> UUID:
    """Insert a minimal active Product directly via SQLAlchemy.

    Avoids spinning the catalog admin API for setup — fewer moving parts
    in the test, and the catalog tests already cover that endpoint.
    """
    sm = get_sessionmaker()
    pid = uuid4()
    suffix = pid.hex[:8]
    async with sm() as s, s.begin():
        p = Product(
            id=pid,
            slug=slug or f"test-product-{suffix}",
            name=f"Test Product {suffix}",
            mother_sku=f"TEST-{suffix.upper()}",
            status=ProductStatus.ACTIVE,
            base_currency="BDT",
            tax_class="standard",
            is_medicine=False,
            requires_prescription=False,
        )
        s.add(p)
    return pid


async def seed_video(
    *,
    product_id: UUID,
    status: str = "ready_for_review",
    hls_url: str | None = "https://cdn.test.example/hls/master.m3u8",
    thumbnail_url: str | None = "https://cdn.test.example/poster.jpg",
    raw_object_key: str | None = "raw/product-videos/test-key/original.mp4",
    duration_seconds: int | None = 5,
    file_size_bytes: int | None = 1024,
    rejection_reason: str | None = None,
    approved_at: datetime | None = None,
    title: str | None = "Test video",
) -> UUID:
    """Insert a product_video row directly, bypassing the upload pipeline.

    Defaults represent a freshly-FFmpeg'd video at ``ready_for_review``
    with HLS + thumbnail URLs already populated — i.e. the state the
    admin sees on /pending. Override ``status`` to set up other states.
    """
    sm = get_sessionmaker()
    vid = uuid4()
    async with sm() as s, s.begin():
        v = ProductVideo(
            id=vid,
            product_id=product_id,
            status=status,
            raw_object_key=raw_object_key,
            hls_url=hls_url,
            thumbnail_url=thumbnail_url,
            duration_seconds=duration_seconds,
            file_size_bytes=file_size_bytes,
            rejection_reason=rejection_reason,
            approved_at=approved_at,
            title=title,
        )
        s.add(v)
    return vid


async def get_video_row(video_id: UUID) -> ProductVideo | None:
    sm = get_sessionmaker()
    async with sm() as s:
        return (
            await s.execute(
                select(ProductVideo).where(ProductVideo.id == video_id),
            )
        ).scalar_one_or_none()


async def count_video_events(*, video_id: UUID) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                select(VideoEvent).where(VideoEvent.video_id == video_id),
            )
        ).scalars().all()
        return len(rows)


def small_mp4_bytes(size: int = 1024) -> bytes:
    """Return ``size`` bytes of garbage prefixed with the standard mp4
    ``ftyp`` atom so anything sniffing the first bytes sees an mp4.

    Real ffmpeg would reject this on probe, but the upload validation
    path (router + service.register_upload) only checks the extension
    + MIME header + size — never the inner bytes — so this fixture is
    enough for upload-flow tests. The actual ffmpeg run is the worker's
    job and is covered by the smoke test (``scripts/smoke_test_video.sh``).
    """
    ftyp = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    pad = b"\x00" * max(0, size - len(ftyp))
    return (ftyp + pad)[:size]


def headers_with_token(token: str) -> dict[str, Any]:
    return {"Authorization": f"Bearer {token}"}
