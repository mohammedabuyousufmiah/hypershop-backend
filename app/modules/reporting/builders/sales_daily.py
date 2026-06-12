"""Daily revenue trend — one row per day in the requested range.

Filters:
  - ``date_from`` (ISO date, default = today-30)
  - ``date_to``   (ISO date, default = today)

Reuses ``DashboardRepository.sales_summary``. We discard the headline
totals and use the per-day series as the row set.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.repository import DashboardRepository
from app.modules.reporting.builders._filters import date_range

COLUMNS: list[dict[str, str]] = [
    {"key": "day", "label": "Date", "type": "date"},
    {"key": "order_count", "label": "Orders", "type": "int"},
    {"key": "revenue", "label": "Revenue (BDT)", "type": "money"},
]


async def build(
    *,
    session: AsyncSession,
    filters: dict[str, Any],
    scope_type: str,  # noqa: ARG001 — scope unused (sales is org-wide)
    current_user_id: Any,  # noqa: ARG001
    max_rows: int,
) -> list[dict[str, Any]]:
    starts_on, ends_on = date_range(filters)
    repo = DashboardRepository(session)
    summary = await repo.sales_summary(starts_on=starts_on, ends_on=ends_on)
    rows = summary.get("daily", [])
    return rows[:max_rows]
