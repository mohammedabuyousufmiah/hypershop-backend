"""Orders-tests conftest.

The shared ``_truncate_between_tests`` fixture in ``tests/conftest.py``
wipes every table after each test, including the ``delivery_zones``
that ``orders.service.place_order`` now requires (Module 27 wiring —
the place_order call quotes a delivery fee from a matching zone and
errors out otherwise). Same pattern as the delivery-tests conftest.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _seed_delivery_zones_for_orders() -> AsyncIterator[None]:
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
