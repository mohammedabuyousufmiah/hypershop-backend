"""Dashboard widgets owned by the customer-care module.

Auto-discovered by ``app.core.registry.dashboard_widgets``.
"""
from __future__ import annotations

from typing import Any

from app.core.registry.dashboard_widgets import (
    DashboardWidget,
    WidgetGroup,
    WidgetType,
)


async def _resolve_sla_breach_alert_list(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    rows = (await session.execute(_t(
        "SELECT id::text, channel, priority, last_message, "
        "       to_char(last_message_at AT TIME ZONE 'UTC', 'HH24:MI') AS hhmm "
        "FROM cc_conversations "
        "WHERE (sla_first_response_breached OR sla_resolution_breached OR handover_required) "
        "  AND status != 'resolved' "
        "ORDER BY last_message_at DESC LIMIT 10"
    ))).all()
    items = []
    for r in rows:
        items.append({
            "severity": "high" if r[2] in ("high", "urgent") else "medium",
            "title": f"{r[1]} · {r[2]}",
            "body": ((r[3] or "")[:80]),
            "href": f"/admin/support?conversation={r[0]}",
            "hint": r[4],
        })
    return {
        "label": "SLA breaches + handovers",
        "items": items,
        "empty_message": "No SLA breaches right now",
    }


WIDGETS: tuple[DashboardWidget, ...] = (
    DashboardWidget(
        key="sla-breaches",
        type=WidgetType.ALERT_LIST,
        title_en="SLA breaches",
        title_bn="SLA লঙ্ঘন",
        group=WidgetGroup.OPERATIONS,
        required_perm="ai_care.view",
        resolver=_resolve_sla_breach_alert_list,
        span=2,
        order=40,
        module="support-agent",
        refresh_interval=30,
    ),
)
