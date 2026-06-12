"""Public Sponsored Products surfaces (storefront-facing).

Phase 1.D: auction + click endpoints are real.
- GET /ads/auction → run the second-price auction, return winners.
- POST /ads/click → atomic wallet debit + click record + campaign spend.
- POST /ads/impressions/batch → still 501 (viewability tracking; MVP
  logs impressions at auction time, viewability post-MVP).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.ads import service as ads_service
from app.modules.ads.schemas import (
    AuctionWinner,
    RecordClickRequest,
    RecordClickResponse,
)

router = APIRouter(prefix="/ads", tags=["ads-public"])


@router.get(
    "/auction",
    summary="Run the sponsored-products auction and serve N winners",
)
async def auction(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    surface: Annotated[str, Query(pattern="^(search|category|pdp_related)$")],
    surface_ref: Annotated[str, Query(min_length=1, max_length=256)],
    limit: Annotated[int, Query(ge=1, le=10)] = 3,
    session_id: Annotated[str | None, Query(max_length=64)] = None,
) -> dict:
    """Returns ``{"winners": [...]}``. Empty list when no eligible ads —
    the storefront renders the organic results unchanged in that case.
    Impressions are persisted server-side so click handlers can find them.
    """
    async with uow.transactional() as session:
        winners = await ads_service.run_auction(
            session,
            surface=surface,
            surface_ref=surface_ref.lower(),
            slot_count=limit,
            session_id=session_id,
        )
    return {"winners": [w.model_dump(mode="json") for w in winners]}


@router.post(
    "/click",
    summary="Record a click on a sponsored slot (atomic debit + spend bump)",
    response_model=RecordClickResponse,
)
async def record_click(
    body: RecordClickRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> RecordClickResponse:
    """Always returns 200 with the redirect target — the storefront
    bounces the user to the product page regardless of whether the
    click was billable (invalid-replay / insufficient-balance cases
    are still navigations the user expects to land somewhere)."""
    async with uow.transactional() as session:
        try:
            result = await ads_service.record_click(
                session,
                impression_id=body.impression_id,
                user_id=body.user_id,
                session_id=body.session_id,
            )
        except ads_service.AdImpressionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RecordClickResponse(
        click_id=int(result["click_id"]),
        charged_minor=int(result["charged_minor"]),
        redirect_product_id=result["redirect_product_id"],
    )


@router.post(
    "/impressions/batch",
    summary="Batch viewability beacons (deferred to post-MVP)",
)
async def batch_impressions() -> dict:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Viewability tracking deferred to post-MVP — auction-time impressions are already logged.",
    )
