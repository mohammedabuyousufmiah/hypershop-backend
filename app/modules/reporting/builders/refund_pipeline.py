"""Refund pipeline single-row summary for a date range.

Filters: ``date_from``, ``date_to``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.repository import DashboardRepository
from app.modules.reporting.builders._filters import date_range

COLUMNS: list[dict[str, str]] = [
    {"key": "pending_count", "label": "Pending #", "type": "int"},
    {"key": "pending_amount", "label": "Pending (BDT)", "type": "money"},
    {"key": "paid_count", "label": "Paid #", "type": "int"},
    {"key": "paid_amount", "label": "Paid (BDT)", "type": "money"},
    {"key": "cancelled_count", "label": "Cancelled #", "type": "int"},
    {"key": "refund_rate", "label": "Refund rate", "type": "ratio"},
]


async def build(
    *,
    session: AsyncSession,
    filters: dict[str, Any],
    scope_type: str,  # noqa: ARG001
    current_user_id: Any,  # noqa: ARG001
    max_rows: int,  # noqa: ARG001
) -> list[dict[str, Any]]:
    starts_on, ends_on = date_range(filters)
    repo = DashboardRepository(session)
    summary = await repo.refund_summary(starts_on=starts_on, ends_on=ends_on)
    return [summary]
