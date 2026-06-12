"""Re-seed IAM reference rows for packing tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _seed_iam() -> AsyncIterator[None]:
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
