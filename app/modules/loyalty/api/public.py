"""Customer-facing loyalty endpoints.

  GET /loyalty/me                  account snapshot (balance, tier, progress)
  GET /loyalty/me/transactions     paginated ledger (newest-first)
  GET /loyalty/me/availability     redemption preview for a given subtotal
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.loyalty.schemas import (
    LoyaltyAccountOut,
    LoyaltyAvailabilityOut,
    LoyaltyTransactionListOut,
    LoyaltyTransactionOut,
)
from app.modules.loyalty.service import LoyaltyService

router = APIRouter(prefix="/loyalty", tags=["loyalty"])

_R = "loyalty.read.self"


@router.get(
    "/me",
    response_model=LoyaltyAccountOut,
    dependencies=[Depends(requires_permission(_R))],
    summary="Caller's loyalty snapshot",
)
async def get_me(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> LoyaltyAccountOut:
    async with uow.transactional() as session:
        svc = LoyaltyService(session)
        snap = await svc.snapshot(principal.user_id)
    return LoyaltyAccountOut(**snap)


@router.get(
    "/me/transactions",
    response_model=LoyaltyTransactionListOut,
    dependencies=[Depends(requires_permission(_R))],
    summary="Caller's loyalty ledger (newest-first)",
)
async def list_transactions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> LoyaltyTransactionListOut:
    offset = (page - 1) * page_size
    async with uow.transactional() as session:
        svc = LoyaltyService(session)
        items, total = await svc.repo.list_txns(
            principal.user_id, offset=offset, limit=page_size
        )
    return LoyaltyTransactionListOut(
        items=[LoyaltyTransactionOut.model_validate(t) for t in items],
        total=total,
    )


@router.get(
    "/me/availability",
    response_model=LoyaltyAvailabilityOut,
    dependencies=[Depends(requires_permission(_R))],
    summary="Maximum redeemable points for a given subtotal",
)
async def get_availability(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    subtotal_minor: int = Query(
        ...,
        ge=0,
        description="Cart/checkout subtotal in paisa (BDT * 100).",
    ),
) -> LoyaltyAvailabilityOut:
    async with uow.transactional() as session:
        svc = LoyaltyService(session)
        out = await svc.availability(
            principal.user_id, subtotal_minor=subtotal_minor
        )
    return LoyaltyAvailabilityOut(**out)
