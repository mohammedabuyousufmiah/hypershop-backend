"""E2E pipeline-test conftest.

The E2E tests exercise the marketplace happy path end-to-end:

  Admin → stock → customer order → packing → delivery (with COD + POD)
  → finance journal entries → rider wallet settlement

Per-module conftests can't all run together because each one re-seeds
state slightly differently; this conftest does just what the E2E test
needs:

- Re-seed IAM permissions/roles (the truncate-between-tests fixture
  wipes them and the per-module conftests handle their own modules).
- Seed the canonical finance chart of accounts (revenue + COGS + COD
  postings need it).
- Register the inventory + finance outbox handlers in that order so
  ``EVT_ORDER_COMPLETED`` triggers consume → COGS posting in sequence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _seed_iam_full_system() -> AsyncIterator[None]:
    from app.core.db.session import get_sessionmaker
    from app.modules.iam.permissions import ALL_PERMISSIONS, ALL_ROLES

    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        for perm in (*ALL_PERMISSIONS, "*"):
            await s.execute(
                text(
                    "INSERT INTO permissions (name) VALUES (:n) "
                    "ON CONFLICT (name) DO NOTHING",
                ),
                {"n": perm},
            )
        for role_spec in ALL_ROLES:
            await s.execute(
                text(
                    """
                    INSERT INTO roles (name, description, is_system)
                    VALUES (:n, :d, :s)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        is_system = EXCLUDED.is_system
                    """,
                ),
                {
                    "n": role_spec.name,
                    "d": role_spec.description,
                    "s": role_spec.is_system,
                },
            )
            role_id = (
                await s.execute(
                    text("SELECT id FROM roles WHERE name = :n"),
                    {"n": role_spec.name},
                )
            ).scalar_one()
            for perm_name in role_spec.permissions:
                perm_id = (
                    await s.execute(
                        text("SELECT id FROM permissions WHERE name = :n"),
                        {"n": perm_name},
                    )
                ).scalar_one()
                await s.execute(
                    text(
                        "INSERT INTO role_permissions (role_id, permission_id) "
                        "VALUES (:r, :p) ON CONFLICT DO NOTHING",
                    ),
                    {"r": role_id, "p": perm_id},
                )
    yield


@pytest.fixture(autouse=True)
async def _seed_finance_chart_of_accounts() -> AsyncIterator[None]:
    from app.core.db.session import get_sessionmaker
    from app.modules.finance.service import FinanceService

    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        await FinanceService(session).ensure_chart_of_accounts()
    yield


@pytest.fixture(autouse=True)
async def _seed_delivery_zones_for_e2e() -> AsyncIterator[None]:
    """Seed canonical delivery zones — orders.service now requires a
    matching zone on every place_order call (Module 27).

    The truncate-between-tests fixture wipes ``delivery_zones`` between
    tests, so we top up here. Idempotent on the unique ``code`` index.
    """
    from app.core.db.session import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(
            text(
                """
                INSERT INTO delivery_zones
                  (code, name, kind, price, currency, cities, is_default, sort_order)
                VALUES
                  ('DHAKA-METRO', 'Dhaka Metro', 'service_area', 50.00, 'BDT',
                   ARRAY['Dhaka','Mirpur','Dhanmondi','Gulshan','Banani','Uttara','Mohammadpur'], true, 10),
                  ('DHAKA-OUTER', 'Greater Dhaka (3PL)', '3pl', 100.00, 'BDT',
                   ARRAY['Savar','Tongi','Narayanganj','Gazipur','Keraniganj'], false, 20),
                  ('OUTSIDE-DHAKA', 'Outside Dhaka (3PL)', '3pl', 130.00, 'BDT',
                   ARRAY[]::varchar[], false, 30)
                ON CONFLICT (code) DO NOTHING
                """,
            ),
        )
    yield


@pytest.fixture(autouse=True)
def _register_outbox_handlers() -> Iterator[None]:
    """Inventory handler MUST register first so ``EVT_ORDER_COMPLETED``
    consume runs before finance reads the resulting CONSUME rows for
    COGS posting.
    """
    from app.modules.finance import handlers as _fin  # noqa: F401
    from app.modules.finance.handlers import register_finance_handlers
    from app.modules.inventory import handlers as _inv  # noqa: F401
    from app.modules.inventory.handlers import register_inventory_handlers

    register_inventory_handlers()
    register_finance_handlers()
    yield
