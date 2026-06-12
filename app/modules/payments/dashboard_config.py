"""Dashboard widgets owned by the payments module.

Auto-discovered by ``app.core.registry.dashboard_widgets``. See the
orders module for the canonical pattern.
"""
from __future__ import annotations

from typing import Any

from app.core.registry.dashboard_widgets import (
    DashboardWidget,
    WidgetGroup,
    WidgetType,
)


async def _resolve_payment_method_pie(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    rows = (await session.execute(_t(
        "SELECT payment_method, count(*) FROM orders "
        "WHERE placed_at >= (now() AT TIME ZONE 'UTC') - interval '30 days' "
        "GROUP BY payment_method ORDER BY count(*) DESC"
    ))).all()
    return {
        "label": "Payment method mix (30d)",
        "slices": [{"label": r[0] or "unknown", "value": int(r[1])} for r in rows],
    }


async def _resolve_run_reconcile_quick_action(session, principal) -> dict[str, Any]:
    return {
        "label": "Run gateway reconciliation",
        "hint": "Match today's bkash settlement file against captured intents",
        "href": "/admin/payments",
        "icon": "Receipt",
        "perm_required": "payments.reconcile",
    }


WIDGETS: tuple[DashboardWidget, ...] = (
    DashboardWidget(
        key="payment-method-mix",
        type=WidgetType.PIE_CHART,
        title_en="Payment method mix",
        title_bn="পেমেন্ট মেথড অনুপাত",
        group=WidgetGroup.REVENUE,
        required_perm="payments.view",
        resolver=_resolve_payment_method_pie,
        order=20,
        module="payments",
        refresh_interval=300,
    ),
    DashboardWidget(
        key="run-reconcile",
        type=WidgetType.QUICK_ACTION,
        title_en="Reconcile gateway",
        title_bn="গেটওয়ে রিকনসাইল",
        group=WidgetGroup.QUICK_ACTIONS,
        required_perm="payments.reconcile",
        resolver=_resolve_run_reconcile_quick_action,
        order=10,
        module="payments",
        refresh_interval=0,
    ),
)
