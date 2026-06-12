"""Dashboard widgets owned by the inventory module.

Auto-discovered by ``app.core.registry.dashboard_widgets``.
"""
from __future__ import annotations

from typing import Any

from app.core.registry.dashboard_widgets import (
    DashboardWidget,
    WidgetGroup,
    WidgetType,
)


async def _resolve_inventory_low_bar(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    from app.modules.admin_config.service import ModuleConfigService
    svc = ModuleConfigService(session)
    threshold = await svc.get_int("inventory", "low_stock_threshold", default=10)
    rows = (await session.execute(_t(
        "SELECT p.name AS sku_title, "
        "       COALESCE(SUM(sb.quantity), 0) AS qty "
        "FROM stock_balances sb "
        "JOIN product_variants pv ON pv.id = sb.variant_id "
        "JOIN products p ON p.id = pv.product_id "
        "GROUP BY p.id, p.name "
        "HAVING COALESCE(SUM(sb.quantity), 0) < :threshold "
        "ORDER BY qty ASC LIMIT 10"
    ), {"threshold": threshold})).all()
    return {
        "label": f"Low-stock SKUs (< {threshold} on hand)",
        "bars": [{"label": (r[0] or "?")[:40], "value": int(r[1])} for r in rows],
    }


WIDGETS: tuple[DashboardWidget, ...] = (
    DashboardWidget(
        key="inventory-low",
        type=WidgetType.BAR_CHART,
        title_en="Low-stock SKUs",
        title_bn="কম স্টক SKU",
        group=WidgetGroup.OPERATIONS,
        required_perm="inventory.read",
        resolver=_resolve_inventory_low_bar,
        span=2,
        order=50,
        module="inventory",
        refresh_interval=120,
    ),
)
