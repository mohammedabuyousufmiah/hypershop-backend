"""Dashboard widgets owned by the rider-wallet module.

Auto-discovered by ``app.core.registry.dashboard_widgets``.
"""
from __future__ import annotations

from typing import Any

from app.core.registry.dashboard_widgets import (
    DashboardWidget,
    WidgetGroup,
    WidgetType,
)


async def _resolve_cod_pending_kpi(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    row = (await session.execute(_t(
        "SELECT COALESCE(SUM(cash_in_hand), 0) AS total, "
        "       count(*) FILTER (WHERE cash_in_hand > 0) AS riders "
        "FROM rider_wallets"
    ))).first()
    return {
        "value": f"৳{float(row[0] or 0):,.0f}",
        "label": "COD on rider hand",
        "subvalue": f"{int(row[1] or 0)} riders carrying cash",
        "subvalue_label": "Distinct holders",
    }


WIDGETS: tuple[DashboardWidget, ...] = (
    DashboardWidget(
        key="cod-pending",
        type=WidgetType.KPI_CARD,
        title_en="COD on rider hand",
        title_bn="রাইডারের হাতে COD",
        group=WidgetGroup.TODAY,
        required_perm="riders.cod_settle",
        resolver=_resolve_cod_pending_kpi,
        order=30,
        module="rider-wallet-ops",
        refresh_interval=60,
    ),
)
