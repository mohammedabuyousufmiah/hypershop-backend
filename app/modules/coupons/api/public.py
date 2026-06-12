"""Customer-facing coupon preview."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_optional_principal
from app.core.security.principal import Principal
from app.modules.coupons.schemas import CouponPreviewIn, CouponPreviewOut
from app.modules.coupons.service import CouponService

router = APIRouter(prefix="/coupons", tags=["coupons"])


@router.post("/preview", response_model=CouponPreviewOut)
async def preview(
    body: CouponPreviewIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[
        Principal | None, Depends(get_optional_principal)
    ] = None,
) -> CouponPreviewOut:
    async with uow.transactional() as session:
        svc = CouponService(session)
        result = await svc.preview(
            code=body.code,
            subtotal_minor=body.subtotal_minor,
            customer_user_id=principal.user_id if principal else None,
        )
    return CouponPreviewOut(**result)
