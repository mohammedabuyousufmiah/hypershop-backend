"""Shared fixtures for every test under ``app/modules/**/tests/``.

Pytest auto-discovers conftest.py files; placing this at ``app/`` makes
``admin_user``, ``logged_in``, and ``registered_user`` available to all
module test packages without each one re-declaring them.

Helper functions that aren't fixtures live in
``app.modules.iam.tests._helpers`` so test files can import them by name.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


@pytest.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    """ASGI test client for app-tree tests.

    Mirrors the fixture in ``tests/conftest.py`` so module-level test
    suites under ``app/modules/.../tests/`` can use it without relying
    on the sibling ``tests/`` conftest being in scope.
    """
    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
async def _truncate_app_tree_between_tests() -> AsyncIterator[None]:
    """Mirror of ``tests/conftest.py``'s `_truncate_between_tests` for
    the app-tree suite. Without this, rows from one test (users,
    products, sellers, etc.) leak into the next and trip unique
    constraints. Runs AFTER each test (see ``yield`` placement).
    """
    yield
    from app.core.db.session import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select tablename from pg_tables where schemaname = 'public' "
                    "and tablename != 'alembic_version'",
                ),
            )
        ).all()
        if rows:
            tables = ", ".join(f'"{r[0]}"' for r in rows)
            await conn.execute(
                text(f"truncate table {tables} restart identity cascade"),
            )


@pytest.fixture(autouse=True)
async def _seed_hypershop_direct_seller() -> AsyncIterator[None]:
    """Migration 0033 seeds a "Hypershop Direct" seller row; the
    truncate fixture wipes it. Re-seed before each test that touches
    sellers / catalog / reviews / qa.
    """
    from app.core.db.session import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(text(
            "INSERT INTO sellers ("
            "business_name, slug, status, commission_percent, "
            "payout_cadence, payout_method"
            ") VALUES ("
            "'Hypershop Direct', 'hypershop-direct', 'approved', 0.00, "
            "'monthly', 'bank_transfer'"
            ") ON CONFLICT (slug) DO NOTHING"
        ))
    yield


@pytest.fixture(autouse=True)
async def _seed_iam_roles_and_default_license() -> AsyncIterator[None]:
    """Idempotent seed of the IAM permissions/roles/role_permissions.

    The shared truncate fixture wipes IAM reference data between tests;
    without re-seeding, any test using ``admin_user`` would fail
    starting with the second test in the process.

    No license is seeded — license is no longer a sales gate. Compliance
    tests seed their own when needed.
    """
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
        # No license seed — license is no longer a sales gate. Compliance
        # tests that need a license seed it themselves.
    yield


@pytest.fixture
async def registered_user(api_client: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    from app.modules.iam.tests._helpers import get_latest_otp_code

    email = "alice@example.com"
    password = "Sup3rSecret!Pass"  # noqa: S105
    resp = await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": "Alice Test",
        },
    )
    assert resp.status_code == 201, resp.text
    code = await get_latest_otp_code()
    verify = await api_client.post(
        "/api/v1/auth/verify-email",
        json={"email": email, "code": code},
    )
    assert verify.status_code == 204, verify.text
    yield {"email": email, "password": password, "user_id": resp.json()["user_id"]}


@pytest.fixture
async def logged_in(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    payload = body.get("data", body) if isinstance(body, dict) else body
    yield {
        "access": payload["tokens"]["access_token"],
        "refresh": payload["tokens"]["refresh_token"],
        "user": payload["user"],
        "headers": {"Authorization": f"Bearer {payload['tokens']['access_token']}"},
        **registered_user,
    }


@pytest.fixture
async def admin_user(api_client: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    """Direct-DB-seeded admin to avoid the chicken-and-egg of self-bootstrap."""
    from sqlalchemy import text

    from app.core.db.session import get_sessionmaker
    from app.core.security.passwords import hash_password
    from app.core.time import utc_now
    from app.modules.iam.models import User, UserStatus

    email = "root@hypershop.dev"
    password = "AdminP@ssw0rdLong!"  # noqa: S105
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        user = User(
            email=email,
            full_name="Root Admin",
            password_hash=hash_password(password),
            status=UserStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        s.add(user)
        await s.flush()
        admin_role_id = (
            await s.execute(text("SELECT id FROM roles WHERE name = 'admin'"))
        ).scalar_one()
        await s.execute(
            text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
            {"u": user.id, "r": admin_role_id},
        )
    login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    # All responses are wrapped in the standard envelope
    # {success, message, data, meta}; the LoginResponse payload (user + tokens)
    # lives under ``data``. Older fixture code read the top level directly,
    # which KeyErrors against the enveloped body.
    payload = body.get("data", body) if isinstance(body, dict) else body
    yield {
        "email": email,
        "password": password,
        "user_id": payload["user"]["id"],
        "access": payload["tokens"]["access_token"],
        "refresh": payload["tokens"]["refresh_token"],
        "headers": {"Authorization": f"Bearer {payload['tokens']['access_token']}"},
    }
