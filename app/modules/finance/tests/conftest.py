"""Per-module fixtures for finance tests.

Two autouse fixtures:

- :func:`_seed_chart_of_accounts` — the canonical chart of accounts is a
  reference table that the truncate-between-tests fixture wipes. We
  re-seed it idempotently before every finance test so service code can
  call ``ensure_chart_of_accounts`` lazily (or assume the rows exist).
- :func:`_register_finance_handlers` — importing the handlers module
  registers them, but the dispatcher's handler dict is process-global; if
  another test imported handlers and the registration was preserved we
  no-op, otherwise we re-register.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest


@pytest.fixture(autouse=True)
async def _seed_chart_of_accounts() -> AsyncIterator[None]:
    from app.core.db.session import get_sessionmaker
    from app.modules.finance.service import FinanceService

    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        await FinanceService(session).ensure_chart_of_accounts()
    yield


@pytest.fixture(autouse=True)
def _register_finance_handlers() -> None:
    """Ensure inventory handlers register first (so on EVT_ORDER_COMPLETED
    inventory's consume runs BEFORE finance's COGS posting reads the
    resulting stock_ledger CONSUME rows). Order matters here because the
    dispatcher invokes handlers in registration order.
    """
    from app.modules.inventory import handlers as _inv  # noqa: F401  side-effect
    from app.modules.finance import handlers as _fin  # noqa: F401  side-effect
    from app.modules.finance.handlers import register_finance_handlers
    from app.modules.inventory.handlers import register_inventory_handlers

    register_inventory_handlers()
    register_finance_handlers()
