"""Stock totals broken down by bucket (available/reserved/blocked/...).

Filter: ``warehouse_code`` (optional).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.repository import DashboardRepository
from app.modules.reporting.builders._filters import opt_int, opt_str

COLUMNS: list[dict[str, str]] = [
    {"key": "bucket", "label": "Bucket", "type": "string"},
    {"key": "units", "label": "Units", "type": "int"},
]


async def build(
    *,
    session: AsyncSession,
    filters: dict[str, Any],
    scope_type: str,  # noqa: ARG001
    current_user_id: Any,  # noqa: ARG001
    max_rows: int,
) -> list[dict[str, Any]]:
    warehouse_code = opt_str(filters, "warehouse_code")
    # The bucket totals report doesn't list low-stock variants — we
    # still pass low_stock_threshold/limit because the repo signature
    # demands them, then ignore the secondary fields.
    repo = DashboardRepository(session)
    summary = await repo.stock_summary(
        warehouse_code=warehouse_code,
        low_stock_threshold=opt_int(
            filters, "low_stock_threshold", default=10, max_value=10000,
        ),
        low_stock_limit=1,
    )
    return summary.get("by_bucket", [])[:max_rows]
