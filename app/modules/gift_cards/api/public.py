"""Customer-facing gift card endpoints.

  POST /gift-cards/check         — anonymous-OK; preview face value + status
  POST /gift-cards/redeem        — authenticated; credit caller's wallet
  GET  /gift-cards/mine          — authenticated; list purchased + redeemed
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.gift_cards.schemas import (
    GiftCardCheckIn,
    GiftCardCheckOut,
    GiftCardListOut,
    GiftCardOut,
    GiftCardRedeemIn,
)
from app.modules.gift_cards.service import GiftCardService

router = APIRouter(prefix="/gift-cards", tags=["gift-cards"])

_R = "loyalty.read.self"


@router.post(
    "/check",
    response_model=GiftCardCheckOut,
    summary="Validate a gift card code without redeeming (anonymous-OK)",
)
async def check_gift_card(
    payload: GiftCardCheckIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> GiftCardCheckOut:
    async with uow.transactional() as session:
        svc = GiftCardService(session)
        row = await svc.check(payload.code)
    if row is None:
        return GiftCardCheckOut(
            valid=False,
            face_value_minor=None,
            currency=None,
            status=None,
            expires_at=None,
        )
    return GiftCardCheckOut(
        valid=row.status == "active",
        face_value_minor=row.face_value_minor,
        currency=row.currency,
        status=row.status,
        expires_at=row.expires_at,
    )


@router.post(
    "/redeem",
    response_model=GiftCardOut,
    dependencies=[Depends(requires_permission(_R))],
    summary="Redeem a gift card to the caller's loyalty balance",
)
async def redeem_gift_card(
    payload: GiftCardRedeemIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> GiftCardOut:
    async with uow.transactional() as session:
        svc = GiftCardService(session)
        row = await svc.redeem(
            raw_code=payload.code, user_id=principal.user_id,
        )
    return GiftCardOut.model_validate(row)


@router.get(
    "/mine",
    response_model=GiftCardListOut,
    dependencies=[Depends(requires_permission(_R))],
    summary="Caller's purchased + redeemed gift cards",
)
async def list_my_gift_cards(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> GiftCardListOut:
    async with uow.transactional() as session:
        svc = GiftCardService(session)
        items, total = await svc.list_for_user(
            user_id=principal.user_id, offset=offset, limit=limit,
        )
    return GiftCardListOut(
        items=[GiftCardOut.model_validate(r) for r in items],
        total=total,
    )
