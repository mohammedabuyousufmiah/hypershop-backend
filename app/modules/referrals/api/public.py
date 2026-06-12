"""Customer-facing referral endpoints.

  GET  /referrals/mine         — code snapshot (auto-creates on first call)
  POST /referrals/attribute    — paste a code (one-shot, only if not already attributed)
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.referrals.schemas import (
    ReferralAttributeAck,
    ReferralAttributeIn,
    ReferralMineOut,
)
from app.modules.referrals.service import ReferralService

router = APIRouter(prefix="/referrals", tags=["referrals"])

# Reuse the loyalty.read.self perm — anyone allowed to see their loyalty
# state can also see their referral status.
_R = "loyalty.read.self"


@router.get(
    "/mine",
    response_model=ReferralMineOut,
    dependencies=[Depends(requires_permission(_R))],
    summary="Caller's referral code + lifetime counters",
)
async def get_mine(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReferralMineOut:
    async with uow.transactional() as session:
        svc = ReferralService(session)
        # Touch get_or_create_code first so a brand-new caller still
        # sees the lazy-allocated code on this same request.
        await svc.get_or_create_code(principal.user_id)
        snap = await svc.snapshot(principal.user_id)
    return ReferralMineOut(**snap)


@router.post(
    "/attribute",
    response_model=ReferralAttributeAck,
    dependencies=[Depends(requires_permission(_R))],
    summary="Attribute the caller to a referrer (one-shot per account)",
)
async def attribute(
    body: ReferralAttributeIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReferralAttributeAck:
    async with uow.transactional() as session:
        svc = ReferralService(session)
        result = await svc.attribute(
            referee_user_id=principal.user_id,
            raw_code=body.code,
        )
    return ReferralAttributeAck(**result)
