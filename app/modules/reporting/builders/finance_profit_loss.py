"""Profit & Loss as a flat row set.

Filters: ``date_from``, ``date_to`` (default = current month-to-date).

Emits revenue lines, COGS, expense lines, and 3 totals as labeled rows.
Single-section layout works better in CSV/XLSX/PDF than the nested
JSON the API normally returns.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.finance.service import FinanceService
from app.modules.reporting.builders._filters import date_range

COLUMNS: list[dict[str, str]] = [
    {"key": "section", "label": "Section", "type": "string"},
    {"key": "account_code", "label": "Code", "type": "string"},
    {"key": "account_name", "label": "Account", "type": "string"},
    {"key": "amount", "label": "Amount (BDT)", "type": "money"},
]


async def build(
    *,
    session: AsyncSession,
    filters: dict[str, Any],
    scope_type: str,  # noqa: ARG001
    current_user_id: Any,  # noqa: ARG001
    max_rows: int,
) -> list[dict[str, Any]]:
    starts_on, ends_on = date_range(filters, default_days=30)
    if starts_on > ends_on:
        starts_on = date(ends_on.year, ends_on.month, 1)
    svc = FinanceService(session)
    pl = await svc.profit_and_loss(starts_on=starts_on, ends_on=ends_on)

    rows: list[dict[str, Any]] = []
    for line in pl.get("revenue_lines", []):
        rows.append({
            "section": "Revenue",
            "account_code": line["account_code"],
            "account_name": line["account_name"],
            "amount": line["amount"],
        })
    rows.append({
        "section": "Revenue",
        "account_code": "",
        "account_name": "— Revenue total",
        "amount": pl.get("revenue_total"),
    })
    for line in pl.get("expense_lines", []):
        rows.append({
            "section": "Expense",
            "account_code": line["account_code"],
            "account_name": line["account_name"],
            "amount": line["amount"],
        })
    rows.append({
        "section": "Summary",
        "account_code": "",
        "account_name": "Cost of goods sold",
        "amount": pl.get("cogs_total"),
    })
    rows.append({
        "section": "Summary",
        "account_code": "",
        "account_name": "Operating expenses",
        "amount": pl.get("operating_expense_total"),
    })
    rows.append({
        "section": "Summary",
        "account_code": "",
        "account_name": "Net income",
        "amount": pl.get("net_income"),
    })
    return rows[:max_rows]
