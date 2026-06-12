"""GET /funnel/kpi/* — dashboard aggregation endpoints (read-only).

All gated by ``funnel.view`` JWT permission.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_session
from app.core.security.principal import Principal
from app.modules.funnel.security import require_view
from app.modules.funnel.services import kpi_service

router = APIRouter()


@router.get("/overview")
async def overview(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    return await kpi_service.overview(db, start_date, end_date)


@router.get("/social")
async def social(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    return await kpi_service.social_kpi(db, start_date, end_date)


@router.get("/website")
async def website(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    return await kpi_service.website_kpi(db, start_date, end_date)


@router.get("/retargeting")
async def retargeting(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    return await kpi_service.retargeting_kpi(db, start_date, end_date)


@router.get("/followups")
async def followups(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    return await kpi_service.followup_kpi(db, start_date, end_date)


@router.get("/privacy")
async def privacy(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    return await kpi_service.privacy_kpi(db, start_date, end_date)


@router.get("/products")
async def products(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    return await kpi_service.product_kpi(db, start_date, end_date, limit)


@router.get("/categories")
async def categories(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    return await kpi_service.category_kpi(db, start_date, end_date, limit)
