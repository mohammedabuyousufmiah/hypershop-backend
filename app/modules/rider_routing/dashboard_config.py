"""Dashboard widgets owned by the rider-routing module.

Auto-discovered by ``app.core.registry.dashboard_widgets``.
"""
from __future__ import annotations

from typing import Any

from app.core.registry.dashboard_widgets import (
    DashboardWidget,
    WidgetGroup,
    WidgetType,
)


async def _resolve_active_riders_kpi(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    on_duty = (await session.execute(_t(
        "SELECT count(DISTINCT rider_id) FROM rider_shifts WHERE ended_at IS NULL"
    ))).scalar_one()
    total = (await session.execute(_t(
        "SELECT count(*) FROM riders WHERE is_active = true"
    ))).scalar_one()
    return {
        "value": int(on_duty or 0),
        "label": "Riders on duty",
        "subvalue": f"of {int(total)} active",
        "subvalue_label": "Total fleet",
    }


WIDGETS: tuple[DashboardWidget, ...] = (
    DashboardWidget(
        key="active-riders",
        type=WidgetType.KPI_CARD,
        title_en="Riders on duty",
        title_bn="ডিউটিতে রাইডার",
        group=WidgetGroup.TODAY,
        required_perm="rider.dispatch",
        resolver=_resolve_active_riders_kpi,
        order=20,
        module="rider-routing",
        refresh_interval=30,
    ),
)
