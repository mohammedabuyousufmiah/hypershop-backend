from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.modules.packing.models import (
    PackingScan,
    PackingSession,
    PackingSessionLine,
)
from app.modules.packing.state import PackingSessionStatus


class PackingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------- Sessions ----------------

    async def get_session(self, session_id: UUID) -> PackingSession | None:
        stmt = (
            select(PackingSession)
            .where(PackingSession.id == session_id)
            .options(selectinload(PackingSession.lines))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_session_locked(self, session_id: UUID) -> PackingSession | None:
        """Lock the session row + its lines for the duration of a scan."""
        stmt = (
            select(PackingSession)
            .where(PackingSession.id == session_id)
            .with_for_update()
        )
        sess = (await self.session.execute(stmt)).scalar_one_or_none()
        if sess is None:
            return None
        await self.session.refresh(sess, attribute_names=("lines",))
        return sess

    async def open_session_for_order(
        self, *, order_id: UUID, opened_by: UUID | None, notes: str | None,
    ) -> PackingSession:
        sess = PackingSession(
            order_id=order_id,
            status=PackingSessionStatus.OPEN.value,
            opened_by=opened_by,
            notes=notes,
        )
        self.session.add(sess)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError(
                "An open packing session already exists for this order.",
            ) from e
        return sess

    async def add_line(self, **fields: Any) -> PackingSessionLine:
        line = PackingSessionLine(**fields)
        self.session.add(line)
        await self.session.flush()
        return line

    async def list_admin(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
    ) -> tuple[Sequence[PackingSession], int]:
        conds: list[Any] = []
        if status is not None:
            conds.append(PackingSession.status == status)
        count_stmt = select(func.count()).select_from(PackingSession)
        list_stmt = (
            select(PackingSession)
            .order_by(PackingSession.opened_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(PackingSession.lines))
        )
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (await self.session.execute(list_stmt)).scalars().all()
        return rows, int(total)

    # ---------------- Scans ----------------

    async def write_scan(self, **fields: Any) -> PackingScan:
        s = PackingScan(**fields)
        self.session.add(s)
        await self.session.flush()
        return s

    async def list_scans(
        self,
        *,
        session_id: UUID,
        offset: int = 0,
        limit: int = 200,
    ) -> Sequence[PackingScan]:
        stmt = (
            select(PackingScan)
            .where(PackingScan.session_id == session_id)
            .order_by(PackingScan.scanned_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    # ---------------- Helpers ----------------

    async def find_open_session_for_order(
        self, order_id: UUID,
    ) -> PackingSession | None:
        stmt = (
            select(PackingSession)
            .where(
                PackingSession.order_id == order_id,
                PackingSession.status == PackingSessionStatus.OPEN.value,
            )
            .options(selectinload(PackingSession.lines))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


def require_session(s: PackingSession | None) -> PackingSession:
    if s is None:
        raise NotFoundError("Packing session not found.")
    return s


def _silence_unused() -> None:  # pragma: no cover
    _ = datetime
