"""Admin / upload tests for the product_videos module.

Covers:
  - invalid extension rejected
  - invalid MIME rejected
  - oversize file rejected
  - happy-path upload creates a product_video row in ``uploaded``
  - max approved-per-product cap returns 409 on the 4th approve

These tests go through the real ASGI app via ``api_client``; storage
is forced to disk-fallback by leaving R2_* unset (default in tests)
and pointing ``PRODUCT_VIDEO_STORAGE_DIR`` at a test-scoped tmp dir.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.modules.product_videos.tests._helpers import (
    headers_with_token,
    seed_product,
    seed_video,
    small_mp4_bytes,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def disk_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
    """Point the storage layer at a writable tmp dir + bust settings cache.

    Without this, the upload handler defaults to ``/var/hypershop/
    product_videos`` which the test process cannot write to.
    """
    storage = tmp_path / "videos"
    storage.mkdir()
    monkeypatch.setenv("PRODUCT_VIDEO_STORAGE_DIR", str(storage))
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield storage
    get_settings.cache_clear()


@pytest.fixture
def tiny_size_cap(monkeypatch: pytest.MonkeyPatch):
    """Force the upload size cap down to 1 MB so we don't have to send
    200+ MB through the test client to verify the rejection path.
    """
    monkeypatch.setenv("PRODUCT_VIDEO_MAX_SIZE_MB", "1")
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# -- 1. invalid extension rejected --------------------------------------


async def test_upload_rejects_invalid_extension(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    disk_storage: Any,
) -> None:
    product_id = await seed_product()

    files = {"file": ("hack.exe", b"MZ\x90\x00", "application/octet-stream")}
    resp = await api_client.post(
        f"/api/v1/product-videos/products/{product_id}/upload",
        headers=headers_with_token(admin_user["access"]),
        files=files,
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "product_video_unsupported_type"


# -- 2. invalid MIME rejected -------------------------------------------


async def test_upload_rejects_invalid_mime(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    disk_storage: Any,
) -> None:
    product_id = await seed_product()

    # Filename has a valid extension but MIME claims it's an image —
    # the router refuses obvious mismatches like image/png on .mp4.
    files = {"file": ("clip.mp4", small_mp4_bytes(), "image/png")}
    resp = await api_client.post(
        f"/api/v1/product-videos/products/{product_id}/upload",
        headers=headers_with_token(admin_user["access"]),
        files=files,
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "product_video_unsupported_type"


# -- 3. oversize file rejected ------------------------------------------


async def test_upload_rejects_oversized_file(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    disk_storage: Any,
    tiny_size_cap: Any,
) -> None:
    product_id = await seed_product()

    # Cap is 1 MB; send 2 MB of bytes — the streaming spool aborts as
    # soon as the running total exceeds the cap.
    too_big = small_mp4_bytes(size=2 * 1024 * 1024)
    files = {"file": ("big.mp4", too_big, "video/mp4")}
    resp = await api_client.post(
        f"/api/v1/product-videos/products/{product_id}/upload",
        headers=headers_with_token(admin_user["access"]),
        files=files,
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "product_video_file_too_large"
    assert body["details"]["max_mb"] == 1


# -- 4. happy-path upload creates a product_video row -------------------


async def test_upload_creates_product_video_row(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    disk_storage: Any,
) -> None:
    product_id = await seed_product()

    payload = small_mp4_bytes(size=8 * 1024)  # 8 KB — well under cap
    files = {"file": ("clip.mp4", payload, "video/mp4")}
    resp = await api_client.post(
        f"/api/v1/product-videos/products/{product_id}/upload",
        headers=headers_with_token(admin_user["access"]),
        files=files,
        data={"title": "Hello demo"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "uploaded"
    video_id = body["video_id"]

    # The bytes hit disk under the tmp storage_dir; the row points at
    # them via raw_object_key. Status is 'uploaded' — worker hasn't run.
    from app.modules.product_videos.tests._helpers import get_video_row
    from uuid import UUID

    row = await get_video_row(UUID(video_id))
    assert row is not None
    assert row.product_id == product_id
    assert row.status == "uploaded"
    assert row.title == "Hello demo"
    assert row.raw_object_key  # populated, format depends on R2 vs disk
    assert row.file_size_bytes == len(payload)


# -- 5. max 3 approved videos per product -------------------------------


async def test_max_three_approved_videos_per_product(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    """The cap is settings.product_video_max_approved_per_product (default 3).

    Approve three videos in turn — all OK. The fourth approve attempt
    raises ProductVideoBadStateError with code product_video_bad_state.
    """
    product_id = await seed_product()
    headers = headers_with_token(admin_user["access"])

    # Seed 4 ready-for-review rows. All have hls_url + thumbnail_url
    # populated so the approval gate doesn't trip on missing output.
    video_ids = [
        await seed_video(product_id=product_id, status="ready_for_review")
        for _ in range(4)
    ]

    # First three approvals succeed.
    for vid in video_ids[:3]:
        resp = await api_client.post(
            f"/api/v1/admin/product-videos/{vid}/approve",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "approved"

    # Fourth approve hits the cap.
    resp = await api_client.post(
        f"/api/v1/admin/product-videos/{video_ids[3]}/approve",
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "product_video_bad_state"
    assert body["details"]["approved_count"] == 3
    assert body["details"]["cap"] == 3
