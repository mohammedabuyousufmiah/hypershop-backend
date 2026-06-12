"""Payment-method split for the requested date range.

Filters: ``date_from``, ``date_to``.

One row per payment method (cod, bkash, sslcommerz, nagad, rocket, ...).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.repository import DashboardRepository
from app.modules.reporting.builders._filters import date_range

COLUMNS: list[dict[str, str]] = [
    {"key": "payment_method", "label": "Payment method", "type": "string"},
    {"key": "order_count", "label": "Orders", "type": "int"},
    {"key": "revenue", "label": "Revenue (BDT)", "type": "money"},
]


async def build(
    *,
    session: AsyncSession,
    filters: dict[str, Any],
    scope_type: str,  # noqa: ARG001
    current_user_id: Any,  # noqa: ARG001
    max_rows: int,
) -> list[dict[str, Any]]:
    starts_on, ends_on = date_range(filters)
    repo = DashboardRepository(session)
    summary = await repo.sales_summary(starts_on=starts_on, ends_on=ends_on)
    rows = summary.get("by_payment_method", [])
    return rows[:max_rows]
