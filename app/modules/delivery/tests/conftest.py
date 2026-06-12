"""Delivery-test conftest.

The shared ``_truncate_between_tests`` fixture in ``tests/conftest.py`` wipes
every table after each test, including the migration-seeded
``delivery_zones`` rows. Most delivery tests assume the seeded zones are
present, so we re-seed them at the start of each test in this package.

Mirrors the IAM-test conftest pattern of "fixture-driven seed top-up".
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _seed_delivery_zones() -> AsyncIterator[None]:
    """Idempotent re-seed of delivery zones AND the IAM reference rows the
    ``admin_user`` fixture relies on. Both get wiped by the shared
    ``_truncate_between_tests`` fixture but neither is created by per-test
    setup; if we don't re-add them, the second test in this package fails.
    """
    from app.core.db.session import get_sessionmaker
    from app.modules.iam.permissions import ALL_PERMISSIONS, ALL_ROLES

    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        # IAM permissions (including the wildcard for the admin role).
        for perm in (*ALL_PERMISSIONS, "*"):
            await s.execute(
                text(
                    "INSERT INTO permissions (name) VALUES (:n) "
                    "ON CONFLICT (name) DO NOTHING",
                ),
                {"n": perm},
            )
        # IAM roles + role→permission grants.
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
                {"n": role_spec.name, "d": role_spec.description, "s": role_spec.is_system},
            )
            role_id = (
                await s.execute(
                    text("SELECT id FROM roles WHERE name = :n"), {"n": role_spec.name},
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

        # Delivery zones.
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
