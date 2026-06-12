"""Admin feature flag CRUD."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.feature_flags.schemas import (
    FeatureFlagCreateIn,
    FeatureFlagListOut,
    FeatureFlagOut,
    FeatureFlagUpdateIn,
)
from app.modules.feature_flags.service import FeatureFlagService

router = APIRouter(prefix="/admin/feature-flags", tags=["admin-feature-flags"])

_W = "feature_flag.write"


@router.get("", response_model=FeatureFlagListOut)
async def list_flags(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
) -> FeatureFlagListOut:
    async with uow.transactional() as session:
        svc = FeatureFlagService(session)
        items, total = await svc.list_all(
            offset=(page - 1) * page_size, limit=page_size
        )
    return FeatureFlagListOut(
        items=[FeatureFlagOut.model_validate(f) for f in items], total=total
    )


@router.post(
    "",
    response_model=FeatureFlagOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_W))],
)
async def create_flag(
    body: FeatureFlagCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> FeatureFlagOut:
    async with uow.transactional() as session:
        svc = FeatureFlagService(session)
        row = await svc.create(
            key=body.key,
            description=body.description,
            is_enabled=body.is_enabled,
            rollout_percent=body.rollout_percent,
        )
    return FeatureFlagOut.model_validate(row)


@router.patch(
    "/{flag_id}",
    response_model=FeatureFlagOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def update_flag(
    flag_id: UUID,
    body: FeatureFlagUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> FeatureFlagOut:
    async with uow.transactional() as session:
        svc = FeatureFlagService(session)
        row = await svc.update(
            flag_id=flag_id,
            description=body.description,
            is_enabled=body.is_enabled,
            rollout_percent=body.rollout_percent,
        )
    return FeatureFlagOut.model_validate(row)


@router.delete(
    "/{flag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_W))],
)
async def delete_flag(
    flag_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> None:
    async with uow.transactional() as session:
        svc = FeatureFlagService(session)
        await svc.delete(flag_id)
