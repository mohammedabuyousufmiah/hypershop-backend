"""Review media storage helpers — phase 2.

Photos are stored under ``{storage_root}/review_media/{review_id}/``
(disk fallback). The Module 35 storage adapter is reused for
``stream_to_disk`` (size + sha256 + atomic write) and ``public_url_for``
(URL resolution that respects ``product_video_public_base_url`` when
the operator points at a CDN).

R2 / Bunny migration for review media is intentionally deferred —
the volume profile is "thousands of small images per month, not
gigabytes of video", and the disk fallback handles that comfortably
on the existing volume mount. When the catalog gets a generic
"images CDN" target, this module's storage call can swap in one place.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import IO
from uuid import UUID

from app.modules.product_videos.storage import (
    public_url_for,
    storage_root,
    stream_to_disk,
)


def _media_dir(review_id: UUID) -> tuple[Path, str]:
    """Returns (absolute_dir, relative_dir) for this review's photos."""
    rel = f"review_media/{review_id}"
    abs_dir = storage_root() / rel
    return abs_dir, rel


def reserve_review_media_path(
    review_id: UUID, *, ext: str,
) -> tuple[Path, str]:
    """Allocate a unique filename inside the review's media dir.

    Returns ``(absolute_target_path, relative_target_path)``. Generates
    a random hex token to avoid collisions when a customer uploads
    multiple photos in the same second.
    """
    abs_dir, rel_dir = _media_dir(review_id)
    abs_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{secrets.token_hex(6)}{ext}"
    return abs_dir / fname, f"{rel_dir}/{fname}"


def store_image(
    *,
    review_id: UUID,
    fobj: IO[bytes],
    ext: str,
    max_bytes: int,
) -> tuple[str, str, int]:
    """Stream the upload to disk under the review's media dir.

    Returns ``(public_url, object_key, size_bytes)``. ``object_key``
    is the relative path stored on the row so future cleanup tooling
    can locate the file without reparsing the URL.
    """
    target, rel = reserve_review_media_path(review_id, ext=ext)
    size, _sha = stream_to_disk(target=target, chunks=fobj, max_bytes=max_bytes)
    url = public_url_for(rel)
    return url, rel, size
