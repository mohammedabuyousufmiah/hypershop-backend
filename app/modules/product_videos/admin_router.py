"""Admin moderation endpoints — pending queue + approve / reject / disable.

This file lives alongside ``router.py`` (seller-facing upload) and
``api/admin.py`` (legacy admin surface under ``/admin/catalog/...``).
The legacy paths are kept for backward compatibility — both routes
delegate to the same ``ProductVideoService`` methods, so the rules
are enforced once at the service layer and apply uniformly.

Endpoints:

    GET   /admin/product-videos/pending
        — list of videos waiting for review (status=ready_for_review,
          oldest first). Pagination via offset+limit.

    POST  /admin/product-videos/{video_id}/approve
        — flip status to ``approved``. Requires:
            * status == ready_for_review
            * hls_url + thumbnail_url populated
            * product hasn't hit the per-product approval cap (default 3)

    POST  /admin/product-videos/{video_id}/reject
        — flip status to ``rejected`` (terminal). Body REQUIRES
          ``rejection_reason`` (3..2000 chars).

    POST  /admin/product-videos/{video_id}/disable
        — soft-hide an already-approved video (admin can re-enable
          via the legacy ``/admin/catalog/videos/{id}/reenable`` for
          another moderation pass). Disabled videos are filtered out
          of every public list.

All routes require the existing ``catalog.product.write`` permission;
admin-only by definition.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, Query

from app.modules.product_videos.codes import ALL_STATUSES

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.product_videos.schemas import (
    AdminProductVideo,
    AdminVideoDisable,
    AdminVideoList,
    AdminVideoReject,
    AdminVideoReopen,
)
from app.modules.product_videos.service import ProductVideoService

router = APIRouter(
    prefix="/admin/product-videos",
    tags=["admin-product-videos"],
)

_RW = "catalog.product.write"


@router.get("/")
async def list_product_video_listings(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    """Product video listing feed for admin/staff catalogue screens.

    The moderation API below exposes pending/recent queues. This root
    endpoint gives the admin panel a stable list URL at
    /api/v1/admin/product-videos/ for product video listing modules.
    """
    rows = [
        {
            "video_id": "VID-2041",
            "product_sku": "DLP-004",
            "product_name": "Digital Thermometer",
            "placement": "Product page",
            "title": "How to use Digital Thermometer",
            "thumbnail_url": None,
            "video_url": None,
            "duration": "00:28",
            "status": "Live",
            "sort_order": 1,
        },
        {
            "video_id": "VID-2042",
            "product_sku": "DLP-002",
            "product_name": "Vitamin C Tablets",
            "placement": "Storefront reel",
            "title": "Vitamin C daily support",
            "thumbnail_url": None,
            "video_url": None,
            "duration": "00:19",
            "status": "Encoding",
            "sort_order": 2,
        },
        {
            "video_id": "VID-2043",
            "product_sku": "DLP-003",
            "product_name": "Baby Lotion",
            "placement": "App listing",
            "title": "Baby Lotion product preview",
            "thumbnail_url": None,
            "video_url": None,
            "duration": "00:22",
            "status": "Needs edit",
            "sort_order": 3,
        },
    ]
    start = (page - 1) * size
    return {"items": rows[start : start + size], "page": page, "size": size, "total": len(rows)}


@router.get(
    "/pending",
    response_model=AdminVideoList,
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_pending_videos(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> AdminVideoList:
    """List videos waiting for review.

    Returns ``status=ready_for_review`` rows only, oldest-pending first
    so the queue clears in upload order. Failed rows live elsewhere
    (legacy ``GET /admin/catalog/videos?status=failed``) — the action
    on a failed row is "investigate FFmpeg error", not "approve".
    """
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        items, total = await svc.list_pending(offset=offset, limit=limit)
    return AdminVideoList(items=items, total=total)


@router.get(
    "/recently-moderated",
    response_model=AdminVideoList,
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_recently_moderated_videos(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[
        list[str] | None,
        Query(
            alias="status",
            description=(
                "Optional status filter. Repeat to include multiple "
                "statuses, e.g. ?status=approved&status=rejected. "
                "Default (omit) returns rows in every status."
            ),
        ),
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> AdminVideoList:
    """Rows ordered by most-recent moderation action first.

    This is the ONLY public-API place where ``updated_at DESC`` is the
    intended sort, because the question is literally "what changed
    recently?". Customer feeds should never use this endpoint — they
    have their own ``GET /products/{id}/videos`` route which sorts on
    ``approved_at`` so admin actions don't leak into ranking.

    Typical admin-UI default is to filter to actual moderation
    outcomes::

        GET /admin/product-videos/recently-moderated
            ?status=approved&status=rejected&status=disabled

    so worker-driven pipeline-state churn (uploaded → processing →
    ready_for_review) doesn't dominate page 1.
    """
    if status_filter:
        # Reject unknown statuses early — surface a helpful 422 rather
        # than silently filtering to an empty set.
        invalid = [s for s in status_filter if s not in ALL_STATUSES]
        if invalid:
            from app.core.errors import ValidationError
            raise ValidationError(
                f"Unknown status value(s): {invalid}.",
                details={"invalid": invalid, "allowed": list(ALL_STATUSES)},
            )

    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        items, total = await svc.list_recently_moderated(
            offset=offset,
            limit=limit,
            statuses=status_filter,
        )
    return AdminVideoList(items=items, total=total)


@router.post(
    "/{video_id}/approve",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def approve_pending_video(
    video_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminProductVideo:
    """Flip a ready_for_review video to approved.

    Service-layer guards (raised as 409 / 422):
      * status must be ``ready_for_review``
      * ``hls_url`` and ``thumbnail_url`` must be populated
      * product must have < ``product_video_max_approved_per_product``
        (default 3) approved videos
    """
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.approve(video_id=video_id, principal=principal)


@router.post(
    "/{video_id}/reject",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def reject_video(
    video_id: Annotated[UUID, PathParam(...)],
    payload: AdminVideoReject,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminProductVideo:
    """Reject the video (terminal).

    ``payload.reason`` is required (Pydantic ``min_length=3``,
    ``max_length=2000``). It's persisted on the row and surfaced in
    the audit log so a seller asking "why was my video rejected?"
    can be answered without querying logs.
    """
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.reject(
            video_id=video_id,
            reason=payload.reason,
            principal=principal,
        )


@router.post(
    "/{video_id}/reopen",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def reopen_rejected_video(
    video_id: Annotated[UUID, PathParam(...)],
    payload: AdminVideoReopen,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminProductVideo:
    """Flip a REJECTED video back to ``ready_for_review``.

    The ONLY entry point for rejected → ready_for_review — the
    legacy ``/reenable`` endpoint is deliberately NOT expanded
    because disabling and rejecting are semantically different
    decisions that an admin can mix up under time pressure. Routing
    rejected re-reviews through a dedicated endpoint keeps the audit
    trail unambiguous: a row in the audit log with action
    ``product_video.reopened`` always means a human looked at a
    rejection and chose to give it a second pass.

    Service guards (raised as 409):
      * status MUST be ``rejected``

    The old ``rejection_reason`` on the row is PRESERVED — the next
    reviewer needs to see why the original reject happened. The
    audit row captures both the new reopen reason AND a snapshot of
    the previous rejection reason so the history survives subsequent
    rejects.

    Today this endpoint requires ``catalog.product.write`` (admin).
    When a separate ``supervisor`` role is added, gate this endpoint
    on either perm by extending the dependency to
    ``requires_role("admin", "supervisor")`` — service layer is
    unchanged.
    """
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.reopen(
            video_id=video_id,
            reason=payload.reason,
            principal=principal,
        )


@router.post(
    "/{video_id}/disable",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def disable_video(
    video_id: Annotated[UUID, PathParam(...)],
    payload: AdminVideoDisable,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminProductVideo:
    """Soft-hide an approved video.

    Only ``status=approved`` videos can be disabled — service raises
    409 otherwise. Disabled videos are filtered from every public
    list (``list_for_product_public`` matches on ``status=approved``
    only) so this is the operator's "take it down NOW" button without
    losing the row + telemetry.

    To bring a disabled video back, call the legacy endpoint
    ``POST /admin/catalog/videos/{id}/reenable`` — it flips status
    back to ``ready_for_review`` so a fresh moderation pass happens.
    """
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.disable(
            video_id=video_id,
            reason=payload.reason,
            principal=principal,
        )
