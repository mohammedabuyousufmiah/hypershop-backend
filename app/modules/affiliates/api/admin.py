"""Affiliate admin endpoints — manage affiliate accounts.

  GET   /admin/affiliates                — paginated list
  PATCH /admin/affiliates/{user_id}      — toggle enabled / set commission_rate
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.affiliates.models import AffiliateRecord

router = APIRouter(prefix="/admin/affiliates", tags=["admin-affiliates"])
_W = "affiliates.admin"


class AffiliateOut(BaseModel):
    id: UUID
    user_id: UUID
    code: str
    commission_rate: str
    enabled: bool
    total_conversions: int
    model_config = ConfigDict(from_attributes=False)


class AffiliateListOut(BaseModel):
    items: list[AffiliateOut]
    total: int


class AffiliateUpdateIn(BaseModel):
    enabled: bool | None = None
    commission_rate: str | None = Field(default=None, description="e.g. '0.05'")


def _to_out(r: AffiliateRecord) -> AffiliateOut:
    return AffiliateOut(
        id=r.id,
        user_id=r.user_id,
        code=r.code,
        commission_rate=str(r.commission_rate),
        enabled=r.enabled,
        total_conversions=r.total_conversions,
    )


@router.get(
    "",
    response_model=AffiliateListOut,
    dependencies=[Depends(requires_permission(_W))],
    summary="List affiliate accounts",
)
async def list_affiliates(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _principal: Annotated[Principal, Depends(get_current_principal)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AffiliateListOut:
    async with uow.transactional() as session:
        total = (
            await session.execute(select(func.count(AffiliateRecord.id)))
        ).scalar_one()
        rows = (
            await session.execute(
                select(AffiliateRecord)
                .order_by(AffiliateRecord.total_conversions.desc())
                .offset(offset).limit(limit)
            )
        ).scalars().all()
    return AffiliateListOut(items=[_to_out(r) for r in rows], total=int(total))


@router.patch(
    "/{user_id}",
    response_model=AffiliateOut,
    dependencies=[Depends(requires_permission(_W))],
    summary="Toggle enabled or update commission rate",
)
async def update_affiliate(
    user_id: UUID,
    payload: AffiliateUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _principal: Annotated[Principal, Depends(get_current_principal)],
) -> AffiliateOut:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                select(AffiliateRecord).where(AffiliateRecord.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Affiliate not found.")
        if payload.enabled is not None:
            row.enabled = payload.enabled
        if payload.commission_rate is not None:
            row.commission_rate = Decimal(payload.commission_rate)
        await session.flush()
    return _to_out(row)
