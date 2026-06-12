"""Delivery throughput summary as a single-row report.

Returns one row with status counts + completion rate + avg minutes
for the requested date range.

Filters: ``date_from``, ``date_to``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.repository import DashboardRepository
from app.modules.reporting.builders._filters import date_range

COLUMNS: list[dict[str, str]] = [
    {"key": "assigned", "label": "Assigned", "type": "int"},
    {"key": "picked_up", "label": "Picked up", "type": "int"},
    {"key": "delivered", "label": "Delivered", "type": "int"},
    {"key": "completed", "label": "Completed", "type": "int"},
    {"key": "cancelled", "label": "Cancelled", "type": "int"},
    {"key": "failed", "label": "Failed", "type": "int"},
    {"key": "in_transit", "label": "In transit (now)", "type": "int"},
    {"key": "awaiting_assignment", "label": "Awaiting assign", "type": "int"},
    {"key": "completion_rate", "label": "Completion rate", "type": "ratio"},
    {
        "key": "avg_minutes_assignment_to_completion",
        "label": "Avg min A→C",
        "type": "decimal",
    },
]


async def build(
    *,
    session: AsyncSession,
    filters: dict[str, Any],
    scope_type: str,  # noqa: ARG001
    current_user_id: Any,  # noqa: ARG001
    max_rows: int,  # noqa: ARG001 — single-row report
) -> list[dict[str, Any]]:
    starts_on, ends_on = date_range(filters)
    repo = DashboardRepository(session)
    summary = await repo.delivery_summary(starts_on=starts_on, ends_on=ends_on)
    return [summary]
