"""Public-but-authenticated upload endpoint for product videos.

The existing module exposes two router groups under
``app.modules.product_videos.api``:

  * ``/admin/catalog/products/{id}/videos``  — admin moderation surface
  * ``/catalog/products/{id}/videos``        — public list + events

This file adds a THIRD surface — ``/product-videos/...`` — designed
for the seller-app upload flow specifically. Keeping it on its own
prefix lets us evolve seller-side rules (rate limits, multi-part
resumable uploads, ownership checks once the catalog gets seller_id
columns) without touching the admin path.

Endpoint::

    POST /api/v1/product-videos/products/{product_id}/upload
        multipart/form-data:
            file       — required, .mp4 or .mov, ≤ 200 MB
            title      — optional
            seller_id  — optional, recorded for analytics

    response 201:
        { "video_id": "<uuid>", "status": "uploaded" }

Pipeline behind the response:

  1. Auth + authorization — auth required; admin OR (when ownership
     data exists in the catalog) seller-equals-product-owner.
  2. MIME + extension + size validation.
  3. Spool bytes to ``/tmp`` while computing sha256 + size; abort with
     ``413`` if it exceeds the configured cap.
  4. ``upload_private_file`` to R2 (or keep on disk in dev).
  5. Insert ``product_videos`` row with status=``uploaded``.
  6. ``arq_pool.enqueue_job("process_product_video", str(video_id))``
     — kicks off FFmpeg + Bunny upload in <1 s. The cron tick remains
     a safety net so a failed enqueue doesn't strand the row.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Path as PathParam,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response

from app.core.config import get_settings
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ForbiddenError
from app.core.logging import get_logger
from app.core.queue import get_arq_pool
from app.core.ratelimit import RateLimit, RateLimiter
from app.core.security.deps import get_current_principal, get_optional_principal
from app.core.security.principal import Principal
from app.modules.catalog.repository import ProductRepository
from app.modules.product_videos.codes import ALL_EVENTS, STATUS_UPLOADED
from app.modules.product_videos.errors import (
    ProductVideoEventInvalidError,
    ProductVideoFileTooLargeError,
    ProductVideoUnsupportedTypeError,
)
from app.modules.product_videos.schemas import (
    ProductVideoUploadResponse,
    VideoEventCreate,
)
from app.modules.product_videos.service import ProductVideoService
from app.modules.product_videos.storage import (
    delete_object,
    private_key,
    r2_enabled,
    reserve_directory,
    stream_to_disk,
    upload_private_file,
)

router = APIRouter(prefix="/product-videos", tags=["product-videos-upload"])
_log = get_logger("hypershop.product_videos.router")

# Stricter than the admin upload's allow-list — this seller-facing
# endpoint accepts only the two containers iOS / Android browsers
# emit by default. Webm / mkv / m4v go through the admin path.
_ALLOWED_EXTS = {".mp4", ".mov"}
_ALLOWED_MIMES = {
    "video/mp4",
    "video/quicktime",  # .mov
    "video/x-quicktime",
}
_ADMIN_PERM = "catalog.product.write"


def _ext_from_filename(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


async def _authorize_upload(
    session,
    principal: Principal,
    product,
) -> None:
    """Enforce admin-OR-owner-seller rules for this upload.

    Phase 2 of the sellers initiative wired the proper authz check
    in ``app.modules.sellers.authz``: admins bypass; seller users
    must be linked to a writable role on the product's seller_id;
    everyone else gets a 403.
    """
    from app.modules.sellers.authz import assert_can_write_product

    if principal.has_permission(_ADMIN_PERM):
        return
    await assert_can_write_product(session, principal, product.id)


@router.post(
    "/products/{product_id}/upload",
    response_model=ProductVideoUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_product_video(
    product_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str | None, Form()] = None,
    seller_id: Annotated[UUID | None, Form()] = None,
) -> ProductVideoUploadResponse:
    settings = get_settings()
    max_bytes = settings.product_video_max_size_mb * 1024 * 1024

    mime = (file.content_type or "").lower()
    ext = _ext_from_filename(file.filename or "")
    if ext not in _ALLOWED_EXTS:
        raise ProductVideoUnsupportedTypeError(
            f"Extension {ext!r} not allowed; expected {sorted(_ALLOWED_EXTS)}.",
            details={"allowed_exts": sorted(_ALLOWED_EXTS)},
        )
    # MIME validation — browsers occasionally lie (especially mobile
    # Safari which sometimes emits ``application/octet-stream`` on
    # .mov), so we treat MIME as advisory and let the extension
    # decide. Still reject obvious mismatches like image/png on a
    # .mp4 extension.
    if not mime.startswith("video/") and mime not in _ALLOWED_MIMES:
        if mime != "" and mime != "application/octet-stream":
            raise ProductVideoUnsupportedTypeError(
                f"MIME {mime!r} is not a recognised video container.",
                details={"mime_type": mime},
            )

    # Authorise BEFORE spooling bytes — no point burning disk + IO if
    # the principal can't write to this product. The authz helper
    # itself queries the seller_users link, so we keep the session
    # open across both calls.
    async with uow.transactional() as session:
        product = await ProductRepository(session).get(product_id)
        if product is None:
            from app.core.errors import NotFoundError
            raise NotFoundError("Product not found.")
        await _authorize_upload(session, principal, product)
        # Sellers phase 2: seller_id must come from the principal's
        # link, not a client form field. The form value (if any) is
        # ignored to prevent a seller from masquerading as another.
        from app.modules.sellers.authz import seller_id_for_user
        derived_seller_id = await seller_id_for_user(
            session, principal.user_id,
        )
        # Admins fall through to the product's owning seller_id (which
        # is already the Hypershop Direct id post-backfill).
        seller_id = derived_seller_id or product.seller_id

    # Pre-allocate the row id so the on-disk / R2 directory name = id.
    video_id = uuid4()
    abs_dir, rel_dir = reserve_directory(video_id)
    target = abs_dir / f"original{ext}"
    use_r2 = r2_enabled()

    try:
        size, _sha = stream_to_disk(
            target=target,
            chunks=file.file,
            max_bytes=max_bytes,
        )
    except OverflowError as e:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProductVideoFileTooLargeError(
            f"Upload exceeded {max_bytes} bytes ({settings.product_video_max_size_mb} MB).",
            details={
                "max_bytes": max_bytes,
                "max_mb": settings.product_video_max_size_mb,
            },
        ) from e
    finally:
        await file.close()

    if use_r2:
        raw_object_key = private_key(f"{rel_dir}/{target.name}")
        try:
            upload_private_file(
                local_path=target,
                object_key=raw_object_key,
                content_type="video/mp4" if ext == ".mp4" else "video/quicktime",
            )
        except Exception:
            from app.modules.product_videos.metrics import (
                r2_upload_errors_total,
            )
            r2_upload_errors_total.inc()
            try:
                target.unlink(missing_ok=True)
                abs_dir.rmdir()
            except OSError:
                pass
            raise
        try:
            target.unlink(missing_ok=True)
            abs_dir.rmdir()
        except OSError:
            pass
    else:
        raw_object_key = f"{rel_dir}/{target.name}"

    # Insert the row inside a transaction. Ownership re-check lives
    # in the service via product lookup; we did the auth check above
    # already so this is just the row write + audit.
    try:
        async with uow.transactional() as session:
            svc = ProductVideoService(session)
            row = await svc.register_upload(
                video_id=video_id,
                product_id=product_id,
                seller_id=seller_id,
                title=title,
                raw_object_key=raw_object_key,
                file_size_bytes=size,
                principal=principal,
            )
    except Exception:
        # Roll back the storage side too if the DB write failed.
        try:
            if use_r2:
                delete_object(raw_object_key)
            else:
                target.unlink(missing_ok=True)
                abs_dir.rmdir()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        raise

    # Dispatch the FFmpeg + Bunny pipeline. Failures here are NOT
    # fatal — the cron tick (`process_pending_videos_job`) will pick
    # the row up within 30 s as a safety net. We log so ops sees the
    # enqueue path degraded.
    try:
        pool = await get_arq_pool()
        await pool.enqueue_job(
            "process_product_video",
            str(video_id),
        )
    except Exception as e:  # noqa: BLE001
        from app.modules.product_videos.metrics import (
            arq_enqueue_failures_total,
        )
        arq_enqueue_failures_total.inc()
        _log.warning(
            "process_product_video_enqueue_failed",
            video_id=str(video_id),
            error=str(e),
        )

    return ProductVideoUploadResponse(
        video_id=video_id,
        status=STATUS_UPLOADED,
    )


# ---------------------------------------------------------------------
#  POST /product-videos/{video_id}/event — telemetry beacon
# ---------------------------------------------------------------------

# Per-(video, session) bucket. 30/min/(video,session) is generous for
# normal players (a typical view fires impression+play+complete = 3
# events plus a couple of pause/resume) and tight enough that a runaway
# loop trips fast. We keep the same scope name as the legacy IP-based
# limiter so a single Redis key prefix tracks all event traffic.
_EVENT_LIMIT = RateLimit(capacity=30, window_seconds=60)


@router.post(
    "/{video_id}/event",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    tags=["product-videos-events"],
)
async def record_video_event(
    video_id: Annotated[UUID, PathParam(...)],
    payload: VideoEventCreate,
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
) -> Response:
    """Record an analytics event for an APPROVED video.

    Anonymous OK — bearer token is optional and only used to stamp
    ``customer_id`` on the row when present (so analytics can split
    authenticated vs anonymous engagement). Service-layer guards make
    sure rows for non-approved videos are silently dropped, which
    matters because a video can transition out of approved (disable /
    reject / reopen) mid-session: we don't want a 404 to bubble up
    into the product page.

    Rate limit: per ``(video_id, session_id)`` when session is given;
    falls back to ``(video_id, ip)`` for server-side fired calls
    (e.g. ATC-after-video from cart endpoints) that don't carry a
    session token. A 429 here is intentionally NOT loud — the
    frontend's ``fireAndForget`` swallows the rejection and the page
    keeps rendering. Rule "analytics failure must not break frontend"
    is enforced by both this swallow on the client and the silent
    no-op behaviour on the server when state is bad.

    Replaces the legacy ``POST /catalog/videos/{video_id}/events``;
    same payload shape, same service method behind the scenes.
    """
    if payload.event_type not in ALL_EVENTS:
        raise ProductVideoEventInvalidError(
            f"Unknown event type: {payload.event_type!r}.",
            details={"allowed": list(ALL_EVENTS)},
        )

    ip = (request.client.host if request.client else "unknown") or "unknown"
    session_part = payload.session_id or f"ip:{ip}"
    identity = f"{video_id}:{session_part}"
    await RateLimiter().check("product_video_event", identity, _EVENT_LIMIT)

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
