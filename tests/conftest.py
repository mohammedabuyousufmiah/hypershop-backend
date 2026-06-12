"""Test environment bootstrap.

Two modes, selected by env:

1. **External services (default when env URLs are set).**
   Set ``HYPERSHOP_TEST_DATABASE_URL`` and ``HYPERSHOP_TEST_REDIS_URL`` to
   point at a Postgres/Redis you already have running. Used on Windows
   without Docker, or when CI provides the services as job dependencies.
   The test database must already exist; migrations are run against it.

2. **Testcontainers (default when no env URL is set).**
   Spins up disposable Postgres + Redis containers. Requires Docker.
   This is the canonical CI path.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command

_EXTERNAL_DB_URL = os.environ.get("HYPERSHOP_TEST_DATABASE_URL")
_EXTERNAL_REDIS_URL = os.environ.get("HYPERSHOP_TEST_REDIS_URL")
_EXTERNAL_MODE = bool(_EXTERNAL_DB_URL and _EXTERNAL_REDIS_URL)


def _async_to_sync_pg_url(async_url: str) -> str:
    """Convert ``postgresql+asyncpg://...`` → ``postgresql+psycopg2://...``."""
    if "+asyncpg" in async_url:
        return async_url.replace("+asyncpg", "+psycopg2", 1)
    if async_url.startswith("postgresql://"):
        return async_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return async_url


# ---------------- container fixtures (only used when not in external mode) ----------------


@pytest.fixture(scope="session")
def _postgres() -> Iterator[Any]:
    if _EXTERNAL_MODE:
        yield None
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        pg.with_command("postgres -c fsync=off -c full_page_writes=off")
        yield pg


@pytest.fixture(scope="session")
def _redis() -> Iterator[Any]:
    if _EXTERNAL_MODE:
        yield None
        return
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as r:
        yield r


# ---------------- env + migration ----------------


@pytest.fixture(scope="session", autouse=True)
def _env(
    _postgres: Any,
    _redis: Any,
) -> Iterator[None]:
    if _EXTERNAL_MODE:
        assert _EXTERNAL_DB_URL is not None and _EXTERNAL_REDIS_URL is not None
        async_url = _EXTERNAL_DB_URL
        sync_url = _async_to_sync_pg_url(async_url)
        redis_url = _EXTERNAL_REDIS_URL
    else:
        assert _postgres is not None and _redis is not None
        host = _postgres.get_container_host_ip()
        port = _postgres.get_exposed_port(5432)
        user = _postgres.username
        password = _postgres.password
        db = _postgres.dbname
        async_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"
        sync_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
        redis_url = f"redis://{_redis.get_container_host_ip()}:{_redis.get_exposed_port(6379)}/0"

    test_env = {
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "WARNING",
        "SERVICE_NAME": "hypershop-test",
        "API_PREFIX": "/api/v1",
        "DATABASE_URL": async_url,
        "DATABASE_SYNC_URL": sync_url,
        "REDIS_URL": redis_url,
        "JWT_SECRET": "test-secret-replace-with-something-at-least-32-chars-long",
        "JWT_ACCESS_TTL_SECONDS": "900",
        "JWT_REFRESH_TTL_SECONDS": "604800",
        "OTP_LENGTH": "6",
        "OTP_TTL_SECONDS": "600",
        "OTP_MAX_ATTEMPTS": "5",
        "PASSWORD_RESET_TTL_SECONDS": "3600",
        "CORS_ORIGINS": "",
        "SMTP_HOST": "localhost",
        "SMTP_PORT": "1025",
        "SMTP_USE_TLS": "false",
        "SMTP_SENDER": "no-reply@hypershop.test",
        "ARQ_QUEUE_NAME": "hypershop:test:jobs",
        "INVENTORY_DEFAULT_WAREHOUSE_CODE": "MAIN",
        "INVENTORY_NEAR_EXPIRY_DAYS": "30",
        "INVENTORY_EXPIRY_CHECK_INTERVAL_MINUTES": "60",
        "WALLET_CREDIT_LIFETIME_DAYS": "30",
        "WALLET_ROLLOVER_PERCENT": "0",
        "WALLET_CURRENCY": "BDT",
        "DELIVERY_POD_DIR": "/tmp/hypershop_test_delivery_pod",
        "DELIVERY_POD_MAX_FILE_BYTES": str(8 * 1024 * 1024),
        "DELIVERY_COD_AUTO_RECONCILE_TOLERANCE_CENTS": "0",
        "VAT_RATE": "0",
    }
    for k, v in test_env.items():
        os.environ[k] = v

    # Drop any cached settings instance so it picks up the new env.
    from app.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    # Run migrations against the chosen DB.
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    if _EXTERNAL_MODE:
        # When using an external DB we don't want to leave half-applied state
        # from a previous run; downgrade to base then re-upgrade. Best-effort —
        # an empty DB will raise on downgrade and that's fine.
        with contextlib.suppress(Exception):
            command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    yield


# ---------------- per-test helpers ----------------


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from app.core.db.session import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        yield session


@pytest.fixture(autouse=True)
async def _truncate_between_tests(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Truncate every project table after each integration test so tests stay
    independent without resorting to per-test transactions (which would hide
    commit-only side effects like the outbox dispatcher).

    Skipped for tests not marked ``integration`` — pure unit tests don't
    touch the DB and creating an asyncpg engine for them just to truncate
    invites event-loop lifecycle issues under pytest-asyncio auto mode.
    """
    yield
    if "integration" not in request.keywords:
        return
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
            await conn.execute(text(f"truncate table {tables} restart identity cascade"))


@pytest.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
