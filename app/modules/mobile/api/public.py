"""Public mobile endpoints — no auth required.

Currently just the anonymous order-tracker. Tracking-by-code requires
the order code AND the recipient phone's last 4 digits, so possessing
just the (short, predictable) order code is not enough to enumerate.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.mobile.schemas import TrackOrderResponse
from app.modules.mobile.service import MobileService

router = APIRouter(prefix="/track", tags=["track"])


@router.get(
    "/orders/{code}",
    response_model=TrackOrderResponse,
    summary="Anonymous order status by code + phone last-4",
    description=(
        "Returns coarse-grained status info for a single order. The caller "
        "must supply the recipient phone's **last four digits** as a check "
        "to prevent enumeration. Wrong phone returns 404 (same as missing "
        "code) so the endpoint reveals nothing about which codes exist."
    ),
)
async def track_order(
    code: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    phone_last4: str = Query(..., min_length=4, max_length=4, pattern=r"^\d{4}$"),
) -> TrackOrderResponse:
    async with uow.transactional() as session:
        svc = MobileService(session)
        data = await svc.track_by_code(code=code, phone_last4=phone_last4)
    return TrackOrderResponse(**data)
