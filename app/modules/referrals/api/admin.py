"""Referrals admin endpoints — view codes + attributions.

  GET /admin/referrals/codes                      — paginated codes
  GET /admin/referrals/attributions               — paginated attributions (filter by status)
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.referrals.models import ReferralAttribution, ReferralCode

router = APIRouter(prefix="/admin/referrals", tags=["admin-referrals"])
_W = "referrals.admin"


class ReferralCodeOut(BaseModel):
    id: UUID
    user_id: UUID
    code: str
    total_referrals: int
    model_config = ConfigDict(from_attributes=True)


class ReferralCodeListOut(BaseModel):
    items: list[ReferralCodeOut]
    total: int


class ReferralAttributionOut(BaseModel):
    id: UUID
    referrer_user_id: UUID
    referee_user_id: UUID
    status: str
    rewarded_at: datetime | None
    rewarded_by_order_id: UUID | None
    model_config = ConfigDict(from_attributes=True)


class ReferralAttributionListOut(BaseModel):
    items: list[ReferralAttributionOut]
    total: int


@router.get(
    "/codes",
    response_model=ReferralCodeListOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def list_codes(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ReferralCodeListOut:
    async with uow.transactional() as session:
        total = (
            await session.execute(select(func.count(ReferralCode.id)))
        ).scalar_one()
        rows = (
            await session.execute(
                select(ReferralCode)
                .order_by(ReferralCode.total_referrals.desc())
                .offset(offset).limit(limit)
            )
        ).scalars().all()
    return ReferralCodeListOut(
        items=[ReferralCodeOut.model_validate(r) for r in rows],
        total=int(total),
    )


@router.get(
    "/attributions",
    response_model=ReferralAttributionListOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def list_attributions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ReferralAttributionListOut:
    async with uow.transactional() as session:
        q = select(ReferralAttribution)
        cnt = select(func.count(ReferralAttribution.id))
        if status_filter:
            q = q.where(ReferralAttribution.status == status_filter)
            cnt = cnt.where(ReferralAttribution.status == status_filter)
        total = (await session.execute(cnt)).scalar_one()
        rows = (
            await session.execute(
                q.order_by(ReferralAttribution.id.desc())
                 .offset(offset).limit(limit)
            )
        ).scalars().all()
    return ReferralAttributionListOut(
        items=[ReferralAttributionOut.model_validate(r) for r in rows],
        total=int(total),
    )
