from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import os

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        # Under pytest external-mode each async test may run on its own event
        # loop. A pooled asyncpg connection created on loop A and reused on
        # loop B raises "attached to a different loop" / "Event loop is closed"
        # / "unknown protocol state". NullPool opens+closes a fresh connection
        # per checkout so nothing is ever carried across loops. Production keeps
        # the real pool (pool_size/max_overflow/recycle).
        if os.environ.get("HYPERSHOP_TEST_DATABASE_URL"):
            from sqlalchemy.pool import NullPool

            _engine = create_async_engine(
                str(settings.database_url),
                poolclass=NullPool,
                pool_pre_ping=True,
                future=True,
            )
        else:
            _engine = create_async_engine(
                str(settings.database_url),
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_max_overflow,
                pool_recycle=settings.database_pool_recycle_seconds,
                pool_pre_ping=True,
                future=True,
            )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def close_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, NO implicit transaction.

    Mutating endpoints must wrap their service calls in
    ``async with uow.transactional() as session`` so the unit-of-work boundary
    is explicit and the audit/outbox writes commit atomically with the change.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.close()
