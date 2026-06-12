from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.modules.returns.models import (
    ReturnRequest,
    ReturnRequestLine,
    ReturnStatusHistory,
)


class ReturnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, request_id: UUID) -> ReturnRequest | None:
        stmt = (
            select(ReturnRequest)
            .where(ReturnRequest.id == request_id)
            .options(
                selectinload(ReturnRequest.lines),
                selectinload(ReturnRequest.history),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_locked(self, request_id: UUID) -> ReturnRequest | None:
        stmt = (
            select(ReturnRequest)
            .where(ReturnRequest.id == request_id)
            .with_for_update()
        )
        rr = (await self.session.execute(stmt)).scalar_one_or_none()
        if rr is None:
            return None
        await self.session.refresh(rr, attribute_names=("lines", "history"))
        return rr

    async def code_exists(self, code: str) -> bool:
        return (
            await self.session.execute(select(ReturnRequest.id).where(ReturnRequest.code == code))
        ).scalar_one_or_none() is not None

    async def create(self, **fields: Any) -> ReturnRequest:
        rr = ReturnRequest(**fields)
        self.session.add(rr)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Return code collision.") from e
        return rr

    async def add_line(self, **fields: Any) -> ReturnRequestLine:
        line = ReturnRequestLine(**fields)
        self.session.add(line)
        await self.session.flush()
        return line

    async def add_history(
        self,
        *,
        return_request_id: UUID,
        from_status: str | None,
        to_status: str,
        transitioned_by: UUID | None,
        reason: str | None,
    ) -> ReturnStatusHistory:
        h = ReturnStatusHistory(
            return_request_id=return_request_id,
            from_status=from_status,
            to_status=to_status,
            transitioned_by=transitioned_by,
            reason=reason,
        )
        self.session.add(h)
        await self.session.flush()
        return h

    async def list_for_customer(
        self,
        *,
        customer_user_id: UUID,
        offset: int,
        limit: int,
        status: str | None = None,
    ) -> tuple[Sequence[ReturnRequest], int]:
        conds: list[Any] = [ReturnRequest.customer_user_id == customer_user_id]
        if status is not None:
            conds.append(ReturnRequest.status == status)
        count_stmt = (
            select(func.count()).select_from(ReturnRequest).where(*conds)
        )
        list_stmt = (
            select(ReturnRequest)
            .where(*conds)
            .order_by(ReturnRequest.requested_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(ReturnRequest.lines))
        )
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (await self.session.execute(list_stmt)).scalars().all()
        return rows, int(total)

    async def list_admin(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
        order_id: UUID | None = None,
    ) -> tuple[Sequence[ReturnRequest], int]:
        conds: list[Any] = []
        if status is not None:
            conds.append(ReturnRequest.status == status)
        if order_id is not None:
            conds.append(ReturnRequest.order_id == order_id)
        count_stmt = select(func.count()).select_from(ReturnRequest)
        list_stmt = (
            select(ReturnRequest)
            .order_by(ReturnRequest.requested_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(ReturnRequest.lines))
        )
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (await self.session.execute(list_stmt)).scalars().all()
        return rows, int(total)


def require_request(rr: ReturnRequest | None) -> ReturnRequest:
    if rr is None:
        raise NotFoundError("Return request not found.")
    return rr
