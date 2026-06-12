"""Dashboard tests need the finance chart of accounts to exist (refund
metrics + COD deposits read finance tables). The truncate-between-tests
fixture wipes them, so re-seed before every test.
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
def _register_handlers() -> None:
    """Register inventory + finance outbox handlers (some tests run order
    flows end-to-end and rely on the resulting JEs / refund records).
    """
    from app.modules.finance import handlers as _fin  # noqa: F401
    from app.modules.finance.handlers import register_finance_handlers
    from app.modules.inventory import handlers as _inv  # noqa: F401
    from app.modules.inventory.handlers import register_inventory_handlers

    register_inventory_handlers()
    register_finance_handlers()
