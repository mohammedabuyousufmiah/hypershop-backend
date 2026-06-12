"""Public, customer-facing video routes.

Three endpoints:

- ``GET /catalog/products/{product_id}/videos``
  Returns up to 5 approved videos for a product. The shape includes
  the absolute HLS URL + thumbnail URL so the frontend has everything
  in one round-trip.

- ``POST /catalog/videos/{video_id}/events``
  Anonymous + rate-limited per IP. Records all six event types (basic
  playback + ATC/Buy attribution). Idempotent per (video, session,
  type) for the playback events; ATC/Buy events are intentionally not
  deduped so multiple conversions count separately.

- ``GET /catalog/videos/files/{path:path}``
  Streams an HLS playlist or segment from the on-disk store. Only
  serves files belonging to ``approved`` videos. When the operator
  has set ``product_video_public_base_url`` we never go through this
  route (urls point straight at the CDN); but the route stays wired
  so single-VM deployments work without any extra reverse proxy.
"""

from __future__ import annotations

import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, Request, status
from fastapi.responses import FileResponse, Response

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError, ValidationError
from app.core.ratelimit import RateLimit, RateLimiter
from app.core.security.deps import get_optional_principal
from app.core.security.principal import Principal
from app.modules.product_videos.codes import ALL_EVENTS
from app.modules.product_videos.errors import (
    ProductVideoEventInvalidError,
    ProductVideoNotFoundError,
)
from app.modules.product_videos.repository import ProductVideoRepository
from app.modules.product_videos.schemas import (
    PublicProductVideoList,
    VideoEventCreate,
)
from app.modules.product_videos.service import ProductVideoService
from app.modules.product_videos.storage import absolute_path

router = APIRouter(prefix="/catalog", tags=["product-videos"])


_EVENT_LIMIT = RateLimit(capacity=60, window_seconds=60)


@router.get(
    "/products/{product_id}/videos",
    response_model=PublicProductVideoList,
)
async def list_product_videos(
    product_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PublicProductVideoList:
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        items = await svc.list_public(product_id=product_id, limit=5)
    return PublicProductVideoList(items=items)


@router.post(
    "/videos/{video_id}/events",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
async def record_video_event(
    video_id: Annotated[UUID, PathParam(...)],
    payload: VideoEventCreate,
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
) -> Response:
    if payload.event_type not in ALL_EVENTS:
        raise ProductVideoEventInvalidError(
            f"Unknown event type: {payload.event_type!r}.",
            details={"allowed": list(ALL_EVENTS)},
        )

    # Per-IP rate limit so a runaway client can't DoS the events table.
    # We use the network IP rather than a session id because session ids
    # are client-controlled — easy to spoof to defeat the limit.
    ip = (request.client.host if request.client else "unknown") or "unknown"
    await RateLimiter().check("product_video_event", ip, _EVENT_LIMIT)

    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        await svc.record_event(
            video_id=video_id,
            event_type=payload.event_type,
            session_id=payload.session_id,
            customer_id=principal.user_id if principal else None,
            watch_seconds=payload.watch_seconds,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- HLS file serving (single-VM fallback) ----------

# Allowed extensions for the on-disk fallback streamer. Refuses anything
# else so a tampered raw_object_key can't expose unrelated files.
_ALLOWED_SUFFIXES = {".m3u8", ".m4s", ".mp4", ".jpg", ".jpeg", ".png"}

# Map suffix → content-type. Browsers + HLS.js are picky about
# application/vnd.apple.mpegurl for .m3u8, so we set it explicitly.
_CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


@router.get("/videos/files/{rel_path:path}")
async def serve_video_file(
    rel_path: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> FileResponse:
    # The relative path always starts with YYYY/MM/DD/<video_id_hex>/...
    parts = rel_path.split("/")
    if len(parts) < 5:
        raise NotFoundError("File not found.")
    try:
        video_id = UUID(parts[3])
    except ValueError as e:
        raise NotFoundError("File not found.") from e

    suffix = os.path.splitext(parts[-1])[1].lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValidationError("Unsupported video file type.")

    # Defence-in-depth: never serve the raw upload through this route.
    # Allowed shapes:
    #   <id>/poster.jpg                      (len == 5)
    #   <id>/hls/<anything>...               (len >= 6, parts[4] == "hls")
    # Anything else (notably <id>/original.<ext>) is rejected so a
    # guessed-UUID URL can't pull the raw original off disk.
    if len(parts) == 5:
        if parts[4] != "poster.jpg":
            raise ProductVideoNotFoundError()
    elif parts[4] != "hls":
        raise ProductVideoNotFoundError()

    async with uow.transactional() as session:
        v = await ProductVideoRepository(session).get(video_id)
        if v is None or v.status != "approved":
            raise ProductVideoNotFoundError()

    abs_path = absolute_path(rel_path)
    if not abs_path.is_file():
        raise NotFoundError("File not found.")

    headers = {
        # Playlists are immutable once written (we never overwrite the
        # files for a given video). Cache hard but allow purge by URL.
        "Cache-Control": "public, max-age=300, s-maxage=86400",
    }
    return FileResponse(
        path=abs_path,
        media_type=_CONTENT_TYPES[suffix],
        headers=headers,
    )
