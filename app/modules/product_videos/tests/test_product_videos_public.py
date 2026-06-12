"""Public-surface tests for the product_videos module.

Covers:
  - pending video NOT in the public list
  - approved video IS in the public list
  - rejected video hidden from public
  - disabled video hidden from public
  - raw_object_key NEVER serialised in any public response
  - event tracking only writes a row for APPROVED videos
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.modules.product_videos.tests._helpers import (
    count_video_events,
    seed_product,
    seed_video,
)

pytestmark = pytest.mark.integration


# ---------- visibility rules ------------------------------------------


async def test_pending_video_not_in_public_list(
    api_client: AsyncClient,
) -> None:
    """A row at status=ready_for_review must NOT appear publicly until
    an admin approves it.
    """
    product_id = await seed_product()
    await seed_video(product_id=product_id, status="ready_for_review")

    resp = await api_client.get(f"/api/v1/products/{product_id}/videos")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": []}


async def test_approved_video_in_public_list(
    api_client: AsyncClient,
) -> None:
    """An approved row IS in the list with hls_url + thumbnail_url.

    Also verifies the schema contract: response only carries the public
    field set ({id, product_id, title, hls_url, thumbnail_url,
    duration_seconds}). Anything else would be a data-leak surface.
    """
    product_id = await seed_product()
    video_id = await seed_video(
        product_id=product_id,
        status="approved",
        hls_url="https://cdn.test.example/abc/hls/master.m3u8",
        thumbnail_url="https://cdn.test.example/abc/poster.jpg",
        duration_seconds=27,
    )

    resp = await api_client.get(f"/api/v1/products/{product_id}/videos")
    assert resp.status_code == 200, resp.text

    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == str(video_id)
    assert item["product_id"] == str(product_id)
    assert item["hls_url"] == "https://cdn.test.example/abc/hls/master.m3u8"
    assert item["thumbnail_url"] == "https://cdn.test.example/abc/poster.jpg"
    assert item["duration_seconds"] == 27
    # Strict schema: no extra fields ever leak in.
    assert set(item.keys()) == {
        "id",
        "product_id",
        "title",
        "hls_url",
        "thumbnail_url",
        "duration_seconds",
    }


async def test_rejected_video_hidden_from_public(
    api_client: AsyncClient,
) -> None:
    product_id = await seed_product()
    await seed_video(
        product_id=product_id,
        status="rejected",
        rejection_reason="Audio too quiet",
    )

    resp = await api_client.get(f"/api/v1/products/{product_id}/videos")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_disabled_video_hidden_from_public(
    api_client: AsyncClient,
) -> None:
    """A previously-approved video flipped to ``disabled`` falls out of
    the customer feed immediately on the next request — no cache, no
    grace period.
    """
    product_id = await seed_product()
    await seed_video(product_id=product_id, status="disabled")

    resp = await api_client.get(f"/api/v1/products/{product_id}/videos")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ---------- raw_object_key never leaks --------------------------------


async def test_raw_object_key_never_in_public_response(
    api_client: AsyncClient,
) -> None:
    """Triple-check the no-raw-key rule:

    1. The serialised JSON must not contain the field ``raw_object_key``.
    2. The literal raw key string must not appear ANYWHERE in the
       response body (defense against future refactors that might
       inadvertently leak it through some other field).
    """
    product_id = await seed_product()
    distinctive_key = "raw/product-videos/leak-detector-secret-xyz/original.mp4"
    await seed_video(
        product_id=product_id,
        status="approved",
        raw_object_key=distinctive_key,
    )

    resp = await api_client.get(f"/api/v1/products/{product_id}/videos")
    assert resp.status_code == 200

    payload = resp.json()
    raw_text = resp.text

    for item in payload["items"]:
        assert "raw_object_key" not in item, (
            "Public response leaked raw_object_key field"
        )
    # Substring check across the whole body — covers e.g. the key
    # accidentally appearing in a debug field, an error message, an
    # extra metadata bag, etc.
    assert distinctive_key not in raw_text, (
        "Raw object key string leaked somewhere in the public payload"
    )


# ---------- event tracking gate ---------------------------------------


async def test_event_tracking_only_for_approved_videos(
    api_client: AsyncClient,
) -> None:
    """``service.record_event`` silently drops events for non-approved
    videos. The endpoint always returns 204 (analytics MUST NOT crash
    the player), but the events table only grows for approved rows.
    """
    product_id = await seed_product()

    pending_vid = await seed_video(
        product_id=product_id, status="ready_for_review",
    )
    rejected_vid = await seed_video(
        product_id=product_id, status="rejected",
    )
    approved_vid = await seed_video(
        product_id=product_id, status="approved",
    )

    payload = {
        "event_type": "impression",
        "session_id": "sess-abc-123",
        "watch_seconds": 0,
    }

    # All three accept the request and return 204 (frontend never sees
    # an error so the page can't break on a stale state transition).
    for vid in (pending_vid, rejected_vid, approved_vid):
        resp = await api_client.post(
            f"/api/v1/product-videos/{vid}/event",
            json=payload,
        )
        assert resp.status_code == 204, resp.text

    # But only the approved video actually wrote a row.
    assert await count_video_events(video_id=pending_vid) == 0
    assert await count_video_events(video_id=rejected_vid) == 0
    assert await count_video_events(video_id=approved_vid) == 1
