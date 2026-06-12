"""Customer-facing affiliate endpoints.

  GET /affiliates/me               — record snapshot (auto-creates)
  GET /affiliates/me/link?slug=    — per-product share URL
  GET /affiliates/me/stats         — running counters
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.affiliates.schemas import (
    AffiliateLinkOut,
    AffiliateRecordOut,
    AffiliateStatsOut,
)
from app.modules.affiliates.service import AffiliateService

router = APIRouter(prefix="/affiliates", tags=["affiliates"])

_R = "loyalty.read.self"


@router.get(
    "/me",
    response_model=AffiliateRecordOut,
    dependencies=[Depends(requires_permission(_R))],
)
async def get_mine(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AffiliateRecordOut:
    async with uow.transactional() as session:
        svc = AffiliateService(session)
        rec = await svc.get_or_create(principal.user_id)
    return AffiliateRecordOut.model_validate(rec)


@router.get(
    "/me/link",
    response_model=AffiliateLinkOut,
    dependencies=[Depends(requires_permission(_R))],
)
async def get_link(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    slug: str = Query(..., min_length=1, max_length=255),
    locale: str = Query(default="en"),
) -> AffiliateLinkOut:
    # Storefront origin — prefer the X-Forwarded-Host / Origin / Referer
    # the FE sends, fall back to a config knob.
    origin = (
        request.headers.get("origin")
        or f"{request.url.scheme}://{request.url.netloc}"
    )
    async with uow.transactional() as session:
        svc = AffiliateService(session)
        rec = await svc.get_or_create(principal.user_id)
        url = svc.build_link(
            base_url=origin, code=rec.code, slug=slug, locale=locale
        )
    return AffiliateLinkOut(
        code=rec.code,
        url=url,
        commission_rate=rec.commission_rate,
        product_slug=slug,
    )


@router.get(
    "/me/stats",
    response_model=AffiliateStatsOut,
    dependencies=[Depends(requires_permission(_R))],
)
async def get_stats(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AffiliateStatsOut:
    async with uow.transactional() as session:
        svc = AffiliateService(session)
        rec = await svc.get_or_create(principal.user_id)
    return AffiliateStatsOut(
        code=rec.code,
        enabled=rec.enabled,
        commission_rate=rec.commission_rate,
        total_conversions=rec.total_conversions,
        total_commission_minor=rec.total_commission_minor,
    )
