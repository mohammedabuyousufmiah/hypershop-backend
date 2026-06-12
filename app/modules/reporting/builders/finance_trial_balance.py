"""Trial balance — every account with non-zero activity as of a date.

Filter: ``as_of`` (default today).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.finance.service import FinanceService

COLUMNS: list[dict[str, str]] = [
    {"key": "account_code", "label": "Code", "type": "string"},
    {"key": "account_name", "label": "Account", "type": "string"},
    {"key": "account_type", "label": "Type", "type": "string"},
    {"key": "debit_total", "label": "Debit (BDT)", "type": "money"},
    {"key": "credit_total", "label": "Credit (BDT)", "type": "money"},
    {"key": "balance", "label": "Balance (BDT)", "type": "money"},
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
    svc = FinanceService(session)
    result = await svc.trial_balance(as_of=as_of)
    return result.get("rows", [])[:max_rows]
