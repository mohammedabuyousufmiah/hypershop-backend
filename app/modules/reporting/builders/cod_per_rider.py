"""Per-rider COD outstanding (lifetime, not date-bounded).

Filters: ``date_from``, ``date_to`` (used only for the headline
``cod_collected_total`` not surfaced here; rider rows are lifetime so
the numbers are operationally actionable).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.repository import DashboardRepository
from app.modules.reporting.builders._filters import date_range

COLUMNS: list[dict[str, str]] = [
    {"key": "rider_code", "label": "Rider code", "type": "string"},
    {"key": "rider_name", "label": "Rider", "type": "string"},
    {"key": "expected_total", "label": "Collected (BDT)", "type": "money"},
    {"key": "deposited_total", "label": "Deposited (BDT)", "type": "money"},
    {"key": "outstanding", "label": "Outstanding (BDT)", "type": "money"},
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
    summary = await repo.cod_summary(
        starts_on=starts_on, ends_on=ends_on, rider_limit=max_rows,
    )
    return summary.get("riders", [])
