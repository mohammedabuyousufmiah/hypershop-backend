"""Admin endpoints for customer_segments — segments + RFM + audience export."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.customer_segments import repository as repo
from app.modules.customer_segments import service
from app.modules.customer_segments.schemas import (
    RfmScoreRead,
    SegmentCreate,
    SegmentRead,
    SegmentUpdate,
)

router = APIRouter(
    prefix="/admin/customer-segments", tags=["admin-customer-segments"],
)

_PERM_VIEW = "customer_segments.view"
_PERM_MANAGE = "customer_segments.manage"


@router.get(
    "/",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List customer segments (paginated)",
)
async def list_segments(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    is_active: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_segments(
            session, is_active=is_active, limit=limit, offset=offset,
        )
    return {
        "items": [
            SegmentRead.model_validate(r).model_dump(mode="json")
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{segment_id}",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Read one segment + estimated size",
)
async def get_segment(
    segment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await repo.get_segment(session, segment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Segment not found.")
    return SegmentRead.model_validate(row).model_dump(mode="json")


@router.post(
    "/",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Create a custom segment",
    status_code=status.HTTP_201_CREATED,
)
async def create_segment(
    body: SegmentCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        existing = await repo.get_segment_by_code(session, body.code)
        if existing is not None:
            raise HTTPException(
                status_code=409, detail="Segment code already exists.",
            )
        row = await repo.create_segment(
            session,
            code=body.code,
            name_en=body.name_en,
            name_bn=body.name_bn,
            description=body.description,
            rule=body.rule,
        )
    return SegmentRead.model_validate(row).model_dump(mode="json")


@router.patch(
    "/{segment_id}",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Update a segment",
)
async def update_segment(
    segment_id: UUID,
    body: SegmentUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await repo.get_segment(session, segment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Segment not found.")
        row = await repo.update_segment(
            session,
            segment_id,
            name_en=body.name_en,
            name_bn=body.name_bn,
            description=body.description,
            rule=body.rule,
            is_active=body.is_active,
        )
    return SegmentRead.model_validate(row).model_dump(mode="json")


@router.delete(
    "/{segment_id}",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Soft-delete a segment (is_active=false)",
)
async def soft_delete_segment(
    segment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await repo.get_segment(session, segment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Segment not found.")
        await repo.update_segment(session, segment_id, is_active=False)
    return {"id": str(segment_id), "is_active": False}


@router.get(
    "/{segment_id}/members",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Paginated customer ids in segment membership table",
)
async def list_members(
    segment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        seg = await repo.get_segment(session, segment_id)
        if seg is None:
            raise HTTPException(status_code=404, detail="Segment not found.")
        rows, total = await repo.list_memberships(
            session, segment_id, limit=limit, offset=offset,
        )
    return {
        "items": [
            {
                "segment_id": str(r.segment_id),
                "customer_user_id": str(r.customer_user_id),
                "added_at": r.added_at.isoformat(),
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/{segment_id}/recompute",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="On-demand materialize this segment's membership table",
)
async def recompute_segment(
    segment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        seg = await repo.get_segment(session, segment_id)
        if seg is None:
            raise HTTPException(status_code=404, detail="Segment not found.")
        count = await service.materialize_segment(session, segment_id)
    return {"segment_id": str(segment_id), "member_count": count}


@router.get(
    "/{segment_id}/export.csv",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Download segment audience as CSV for ad-platform retargeting",
)
async def export_csv(
    segment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=50000)] = 10000,
) -> StreamingResponse:
    async with uow.transactional() as session:
        seg = await repo.get_segment(session, segment_id)
        if seg is None:
            raise HTTPException(status_code=404, detail="Segment not found.")
        body, ctype, _n = await service.export_audience(
            session, segment_id, fmt="csv", limit=limit,
        )
    fname = f"segment_{seg.code}_audience.csv"
    return StreamingResponse(
        iter([body]),
        media_type=ctype,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get(
    "/rfm/{customer_user_id}",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Read one customer's RFM score (admin debug)",
)
async def get_rfm(
    customer_user_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await repo.get_rfm_score(session, customer_user_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail="RFM score not yet computed.",
            )
    return RfmScoreRead.model_validate(row).model_dump(mode="json")
