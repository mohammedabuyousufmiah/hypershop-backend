from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_sessionmaker

_current_session: ContextVar[AsyncSession | None] = ContextVar("uow_session", default=None)


def current_session() -> AsyncSession:
    """Return the session bound to the current unit of work.

    Used by services (audit, outbox, etc.) that must enroll in the same
    transaction as the caller without taking the session as a parameter.
    Raises if called outside ``UnitOfWork.transactional()``.
    """
    sess = _current_session.get()
    if sess is None:
        raise RuntimeError(
            "current_session() called outside an active UnitOfWork.transactional() scope",
        )
    return sess


class UnitOfWork:
    """Owns a single AsyncSession + transaction boundary.

    Usage:

        async with uow.transactional() as session:
            await user_repo(session).create(...)
            # implicit commit on exit; rollback on exception

    Nested calls reuse the outer session and use SAVEPOINTs for nested rollback,
    so a service method calling another service method does not create a second
    DB transaction.
    """

    def __init__(self) -> None:
        self._sessionmaker = get_sessionmaker()

    @asynccontextmanager
    async def transactional(self) -> AsyncIterator[AsyncSession]:
        existing = _current_session.get()
        if existing is not None:
            async with existing.begin_nested():
                yield existing
            return

        async with self._sessionmaker() as session:
            token = _current_session.set(session)
            try:
                async with session.begin():
                    yield session
            finally:
                _current_session.reset(token)


def get_uow() -> UnitOfWork:
    """FastAPI dependency factory."""
    return UnitOfWork()
