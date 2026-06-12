"""Daily-close history — recent end-of-day snapshots.

Filter: ``limit`` (default 30, max 365).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.finance.repository import DailyCloseRepository
from app.modules.reporting.builders._filters import opt_int

COLUMNS: list[dict[str, str]] = [
    {"key": "closing_date", "label": "Date", "type": "date"},
    {"key": "entry_count", "label": "Entries", "type": "int"},
    {"key": "total_debits", "label": "Debits (BDT)", "type": "money"},
    {"key": "total_credits", "label": "Credits (BDT)", "type": "money"},
    {"key": "revenue_total", "label": "Revenue (BDT)", "type": "money"},
    {"key": "cogs_total", "label": "COGS (BDT)", "type": "money"},
    {"key": "cash_in_total", "label": "Cash in (BDT)", "type": "money"},
    {"key": "cash_out_total", "label": "Cash out (BDT)", "type": "money"},
]


async def build(
    *,
    session: AsyncSession,
    filters: dict[str, Any],
    scope_type: str,  # noqa: ARG001
    current_user_id: Any,  # noqa: ARG001
    max_rows: int,
) -> list[dict[str, Any]]:
    limit = opt_int(filters, "limit", default=30, min_value=1, max_value=365)
    limit = min(limit, max_rows)
    repo = DailyCloseRepository(session)
    closes = await repo.list_recent(limit=limit)
    return [
        {
            "closing_date": c.closing_date,
            "entry_count": int(c.entry_count),
            "total_debits": c.total_debits,
            "total_credits": c.total_credits,
            "revenue_total": c.revenue_total,
            "cogs_total": c.cogs_total,
            "cash_in_total": c.cash_in_total,
            "cash_out_total": c.cash_out_total,
        }
        for c in closes
    ]
