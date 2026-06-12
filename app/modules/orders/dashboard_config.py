"""Dashboard widgets owned by the orders module.

Auto-discovered by ``app.core.registry.dashboard_widgets._discover_per_module_widgets``
— no registration call needed, just export ``WIDGETS``.

Resolvers live inline so the module owns its own data shapes; future
edits to order schema don't have to round-trip through a central
registry file.
"""
from __future__ import annotations

from typing import Any

from app.core.registry.dashboard_widgets import (
    DashboardWidget,
    WidgetGroup,
    WidgetType,
)


async def _resolve_orders_today_kpi(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    row = (await session.execute(_t(
        "SELECT count(*) AS n, COALESCE(SUM(grand_total), 0) AS gross "
        "FROM orders WHERE placed_at::date = (now() AT TIME ZONE 'UTC')::date"
    ))).first()
    return {
        "value": int(row[0] or 0),
        "label": "Orders today",
        "subvalue": f"৳{row[1]:,.0f}" if row and row[1] else "৳0",
        "subvalue_label": "Gross volume",
    }


async def _resolve_orders_by_status_bar(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    rows = (await session.execute(_t(
        "SELECT status, count(*) FROM orders "
        "WHERE placed_at >= (now() AT TIME ZONE 'UTC') - interval '30 days' "
        "GROUP BY status ORDER BY count(*) DESC"
    ))).all()
    return {
        "label": "Orders by status (last 30 days)",
        "bars": [{"label": r[0], "value": int(r[1])} for r in rows],
    }


async def _resolve_recent_orders_table(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    rows = (await session.execute(_t(
        "SELECT code, status, payment_method, "
        "       to_char(grand_total, 'FM999,999,990.00') AS amt, "
        "       to_char(placed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') AS at "
        "FROM orders ORDER BY placed_at DESC LIMIT 10"
    ))).all()
    return {
        "label": "Recent orders",
        "columns": ["Code", "Status", "Method", "Amount", "Placed (UTC)"],
        "rows": [[r[0], r[1], r[2] or "—", r[3], r[4]] for r in rows],
    }


WIDGETS: tuple[DashboardWidget, ...] = (
    DashboardWidget(
        key="orders-today",
        type=WidgetType.KPI_CARD,
        title_en="Orders today",
        title_bn="আজকের অর্ডার",
        group=WidgetGroup.TODAY,
        required_perm="orders.view",
        resolver=_resolve_orders_today_kpi,
        order=10,
        module="orders",
        refresh_interval=60,
    ),
    DashboardWidget(
        key="orders-by-status",
        type=WidgetType.BAR_CHART,
        title_en="Orders by status",
        title_bn="স্ট্যাটাস অনুযায়ী অর্ডার",
        group=WidgetGroup.OPERATIONS,
        required_perm="orders.view",
        resolver=_resolve_orders_by_status_bar,
        span=2,
        order=20,
        module="orders",
        refresh_interval=60,
    ),
    DashboardWidget(
        key="recent-orders",
        type=WidgetType.TABLE,
        title_en="Recent orders",
        title_bn="সর্বশেষ অর্ডার",
        group=WidgetGroup.OPERATIONS,
        required_perm="orders.view",
        resolver=_resolve_recent_orders_table,
        span=4,
        order=30,
        module="orders",
        refresh_interval=30,
    ),
)
