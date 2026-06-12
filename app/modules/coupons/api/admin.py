"""Admin coupon CRUD."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.coupons.schemas import CouponCreateIn, CouponListOut, CouponOut
from app.modules.coupons.service import CouponService

router = APIRouter(prefix="/admin/coupons", tags=["admin-coupons"])

_W = "promotion.write"


@router.get("", response_model=CouponListOut)
async def list_coupons(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    only_active: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
) -> CouponListOut:
    async with uow.transactional() as session:
        svc = CouponService(session)
        items, total = await svc.list_all(
            only_active=only_active,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
    return CouponListOut(
        items=[CouponOut.model_validate(c) for c in items], total=total
    )


@router.post(
    "",
    response_model=CouponOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_W))],
)
async def create_coupon(
    body: CouponCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CouponOut:
    async with uow.transactional() as session:
        svc = CouponService(session)
        row = await svc.create(
            **body.model_dump(),
            created_by=principal.user_id,
        )
    return CouponOut.model_validate(row)


@router.delete(
    "/{coupon_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_W))],
)
async def delete_coupon(
    coupon_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> None:
    async with uow.transactional() as session:
        svc = CouponService(session)
        await svc.delete(coupon_id)
