"""Admin analytics — paginated event browser + graphical overview."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.analytics.schemas import (
    AnalyticsEventListOut,
    AnalyticsEventOut,
)
from app.modules.analytics.service import AnalyticsService

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"])

_R = "dashboard.read"


@router.get(
    "/events",
    response_model=AnalyticsEventListOut,
    dependencies=[Depends(requires_permission(_R))],
)
async def list_events(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    event_type: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> AnalyticsEventListOut:
    offset = (page - 1) * page_size
    async with uow.transactional() as session:
        svc = AnalyticsService(session)
        items, total = await svc.list_admin(
            event_type=event_type, offset=offset, limit=page_size
        )
    return AnalyticsEventListOut(
        items=[AnalyticsEventOut.model_validate(e) for e in items],
        total=total,
    )


@router.get(
    "/overview",
    summary="Graphical analytics overview (KPIs + trend + events + device + funnel).",
    dependencies=[Depends(requires_permission(_R))],
)
async def overview(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, object]:
    """Composite for the graphical analytics surface. Reads
    ``analytics_events`` only; every probe runs in its own SAVEPOINT so a
    missing column/table degrades to empty instead of 500-ing. Never 500s.
    """
    async with uow.transactional() as session:

        async def one(sql: str, default: int = 0) -> int:
            try:
                async with session.begin_nested():
                    return int((await session.execute(text(sql))).scalar_one())
            except Exception:
                return default

        async def rws(sql: str) -> list:
            try:
                async with session.begin_nested():
                    return list((await session.execute(text(sql))).all())
            except Exception:
                return []

        events = await one("SELECT count(*) FROM analytics_events WHERE created_at >= current_date - 13")
        sessions = await one("SELECT count(DISTINCT session_id) FROM analytics_events WHERE created_at >= current_date - 13")
        purchases = await one("SELECT count(*) FROM analytics_events WHERE created_at >= current_date - 13 AND event_type ILIKE '%purchase%'")
        conversion = round(purchases / sessions * 100, 2) if sessions else 0.0

        tr = await rws(
            "SELECT created_at::date AS d, count(*) AS e, count(DISTINCT session_id) AS s "
            "FROM analytics_events WHERE created_at >= current_date - 13 GROUP BY d ORDER BY d")
        byday = {str(r[0]): (int(r[1]), int(r[2])) for r in tr}
        trend = []
        for i in range(13, -1, -1):
            d = date.today() - timedelta(days=i)
            e, s = byday.get(str(d), (0, 0))
            trend.append({"day": d.strftime("%m-%d"), "events": e, "sessions": s})

        events_by_type = [
            {"label": str(r[0]), "value": int(r[1])}
            for r in await rws(
                "SELECT event_type, count(*) FROM analytics_events GROUP BY event_type ORDER BY count(*) DESC LIMIT 8")
        ]
        device_split = [
            {"label": str(r[0] or "unknown"), "value": int(r[1])}
            for r in await rws(
                "SELECT properties->>'device' AS dev, count(DISTINCT session_id) AS s "
                "FROM analytics_events GROUP BY dev ORDER BY s DESC NULLS LAST LIMIT 5")
        ]
        # Funnel — fixed step names (constants, not user input → safe to inline).
        _STEPS = [("View", "page_view"), ("Product", "product_view"),
                  ("Cart", "add_to_cart"), ("Checkout", "begin_checkout"), ("Purchase", "purchase")]
        funnel = [
            {"label": lbl, "value": await one(f"SELECT count(*) FROM analytics_events WHERE event_type = '{ev}'")}
            for lbl, ev in _STEPS
        ]

        return {
            "kpis": {"sessions": sessions, "events": events, "conversion_pct": conversion, "purchases": purchases},
            "trend": trend,
            "events_by_type": events_by_type,
            "device_split": device_split,
            "funnel": funnel,
        }
