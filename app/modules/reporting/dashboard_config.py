"""Dashboard widgets owned by the reporting module.

Auto-discovered by ``app.core.registry.dashboard_widgets``.
"""
from __future__ import annotations

from typing import Any

from app.core.registry.dashboard_widgets import (
    DashboardWidget,
    WidgetGroup,
    WidgetType,
)


async def _resolve_revenue_7d_line(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    rows = (await session.execute(_t(
        "SELECT to_char(placed_at::date, 'YYYY-MM-DD') AS day, "
        "       COALESCE(SUM(grand_total), 0) AS revenue "
        "FROM orders "
        "WHERE placed_at >= (now() AT TIME ZONE 'UTC') - interval '7 days' "
        "GROUP BY day ORDER BY day"
    ))).all()
    return {
        "label": "Revenue (last 7 days)",
        "series": [{"ts": r[0], "value": float(r[1] or 0)} for r in rows],
        "y_unit": "BDT",
    }


WIDGETS: tuple[DashboardWidget, ...] = (
    DashboardWidget(
        key="revenue-7d",
        type=WidgetType.LINE_CHART,
        title_en="Revenue (last 7 days)",
        title_bn="রাজস্ব (গত ৭ দিন)",
        group=WidgetGroup.REVENUE,
        required_perm="reports.view",
        resolver=_resolve_revenue_7d_line,
        span=2,
        order=10,
        module="reporting",
        refresh_interval=300,
    ),
)
