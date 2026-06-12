"""Variants whose available stock is at or below ``low_stock_threshold``.

Filters:
  - ``warehouse_code`` (optional)
  - ``low_stock_threshold`` (default 10)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.repository import DashboardRepository
from app.modules.reporting.builders._filters import opt_int, opt_str

COLUMNS: list[dict[str, str]] = [
    {"key": "variant_id", "label": "Variant ID", "type": "uuid"},
    {"key": "variant_sku", "label": "SKU", "type": "string"},
    {"key": "product_name", "label": "Product", "type": "string"},
    {"key": "available_units", "label": "Available units", "type": "int"},
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
    threshold = opt_int(
        filters, "low_stock_threshold", default=10, max_value=10000,
    )
    repo = DashboardRepository(session)
    summary = await repo.stock_summary(
        warehouse_code=warehouse_code,
        low_stock_threshold=threshold,
        low_stock_limit=max_rows,
    )
    return summary.get("low_stock_variants", [])
