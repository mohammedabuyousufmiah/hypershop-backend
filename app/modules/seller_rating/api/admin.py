"""Admin seller rating endpoints — list, read, snapshots, suspend, recompute."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.seller_rating import repository as repo
from app.modules.seller_rating import service
from app.modules.seller_rating.schemas import (
    SellerRatingListResponse,
    SellerRatingRead,
    SellerRatingSnapshotListResponse,
    SellerRatingSnapshotRead,
    SuspendRequest,
)

router = APIRouter(
    prefix="/admin/seller-ratings", tags=["admin-seller-ratings"],
)

_PERM_VIEW = "seller_ratings.view"
_PERM_MANAGE = "seller_ratings.manage"


@router.get(
    "",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List seller ratings",
)
async def list_ratings(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    tier: Annotated[str | None, Query()] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_ratings(
            session,
            tier=tier,
            min_score=min_score,
            limit=limit,
            offset=offset,
        )
    return SellerRatingListResponse(
        items=[SellerRatingRead.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")


@router.get(
    "/{seller_id}",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Read a seller's current rating",
)
async def read_one(
    seller_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await repo.get_rating(session, seller_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Rating not found.")
        snaps = await repo.list_snapshots(session, seller_id, limit=1)
    out = SellerRatingRead.model_validate(row).model_dump(mode="json")
    out["latest_snapshot"] = (
        SellerRatingSnapshotRead.model_validate(snaps[0]).model_dump(mode="json")
        if snaps else None
    )
    return out


@router.get(
    "/{seller_id}/snapshots",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Historical snapshots for a seller",
)
async def list_snapshots(
    seller_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=180)] = 30,
) -> dict:
    async with uow.transactional() as session:
        rows = await repo.list_snapshots(session, seller_id, limit=limit)
    return SellerRatingSnapshotListResponse(
        items=[SellerRatingSnapshotRead.model_validate(r) for r in rows],
        total=len(rows),
    ).model_dump(mode="json")


@router.post(
    "/{seller_id}/suspend",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Admin override: suspend seller (force tier=suspended)",
)
async def suspend(
    seller_id: UUID,
    body: SuspendRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        return await service.suspend_seller(session, seller_id, body.reason)


@router.post(
    "/{seller_id}/unsuspend",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Lift suspension and recompute",
)
async def unsuspend(
    seller_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        return await service.unsuspend_seller(session, seller_id)


@router.post(
    "/{seller_id}/recompute",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="On-demand recompute",
)
async def recompute(
    seller_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        return await service.compute_rating_for_seller(session, seller_id)
