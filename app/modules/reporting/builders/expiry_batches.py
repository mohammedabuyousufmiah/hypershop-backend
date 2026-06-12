"""Batches expired or expiring within ``horizon_days``.

Filters:
  - ``as_of`` (default today)
  - ``horizon_days`` (default 30, max 365)
  - ``warehouse_code`` (optional)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.repository import DashboardRepository
from app.modules.reporting.builders._filters import opt_int, opt_str

COLUMNS: list[dict[str, str]] = [
    {"key": "batch_number", "label": "Batch", "type": "string"},
    {"key": "variant_sku", "label": "SKU", "type": "string"},
    {"key": "product_name", "label": "Product", "type": "string"},
    {"key": "expiry_date", "label": "Expiry", "type": "date"},
    {"key": "days_to_expiry", "label": "Days left", "type": "int"},
    {"key": "units_in_stock", "label": "Units", "type": "int"},
]


async def build(
    *,
    session: AsyncSession,
    filters: dict[str, Any],
    scope_type: str,  # noqa: ARG001
    current_user_id: Any,  # noqa: ARG001
    max_rows: int,
) -> list[dict[str, Any]]:
    as_of_str = filters.get("as_of")
    if isinstance(as_of_str, str) and as_of_str:
        try:
            as_of = date.fromisoformat(as_of_str[:10])
        except ValueError:
            as_of = date.today()
    else:
        as_of = date.today()
    horizon_days = opt_int(
        filters, "horizon_days", default=30, min_value=1, max_value=365,
    )
    warehouse_code = opt_str(filters, "warehouse_code")
    repo = DashboardRepository(session)
    summary = await repo.expiry_summary(
        as_of=as_of,
        horizon_days=horizon_days,
        warehouse_code=warehouse_code,
        limit=max_rows,
    )
    return summary.get("batches", [])
