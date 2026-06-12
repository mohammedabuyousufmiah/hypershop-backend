from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.ai.models import AIProposal, AIUsageEvent


class AIProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> AIProposal:
        p = AIProposal(**fields)
        self.session.add(p)
        await self.session.flush()
        return p

    async def get(self, proposal_id: UUID) -> AIProposal | None:
        return await self.session.get(AIProposal, proposal_id)

    async def list_filtered(
        self,
        *,
        kind: str | None,
        status: str | None,
        requested_by: UUID | None,
        reference_type: str | None,
        reference_id: UUID | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[AIProposal], int]:
        conds: list[Any] = []
        if kind is not None:
            conds.append(AIProposal.kind == kind)
        if status is not None:
            conds.append(AIProposal.status == status)
        if requested_by is not None:
            conds.append(AIProposal.requested_by == requested_by)
        if reference_type is not None:
            conds.append(AIProposal.reference_type == reference_type)
        if reference_id is not None:
            conds.append(AIProposal.reference_id == reference_id)

        count_stmt = select(func.count()).select_from(AIProposal)
        list_stmt = select(AIProposal).order_by(AIProposal.created_at.desc())
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        rows = (
            await self.session.execute(list_stmt.offset(offset).limit(limit))
        ).scalars().all()
        return rows, total


class AIUsageEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> AIUsageEvent:
        e = AIUsageEvent(**fields)
        self.session.add(e)
        await self.session.flush()
        return e

    async def list_recent(
        self,
        *,
        kind: str | None,
        success: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[AIUsageEvent], int]:
        conds: list[Any] = []
        if kind is not None:
            conds.append(AIUsageEvent.kind == kind)
        if success is not None:
            conds.append(AIUsageEvent.success == success)

        count_stmt = select(func.count()).select_from(AIUsageEvent)
        list_stmt = select(AIUsageEvent).order_by(
            AIUsageEvent.occurred_at.desc(),
        )
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        rows = (
            await self.session.execute(list_stmt.offset(offset).limit(limit))
        ).scalars().all()
        return rows, total


def require_proposal(p: AIProposal | None) -> AIProposal:
    if p is None:
        raise NotFoundError("AI proposal not found.")
    return p
