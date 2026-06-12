"""Fraud service."""
from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.fraud.models import FraudAssessment


class FraudService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(
        self,
        *,
        decision: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[FraudAssessment], int]:
        base = select(FraudAssessment)
        if decision:
            base = base.where(FraudAssessment.decision == decision)
        items = (
            await self.session.execute(
                base.order_by(FraudAssessment.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        total_stmt = select(func.count()).select_from(FraudAssessment)
        if decision:
            total_stmt = total_stmt.where(FraudAssessment.decision == decision)
        total = int((await self.session.execute(total_stmt)).scalar_one())
        return items, total

    async def get(self, assess_id: UUID) -> FraudAssessment | None:
        return (
            await self.session.execute(
                select(FraudAssessment).where(FraudAssessment.id == assess_id)
            )
        ).scalar_one_or_none()

    async def create(self, **fields) -> FraudAssessment:
        row = FraudAssessment(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def decide(
        self,
        *,
        assess_id: UUID,
        decision: str,
        decision_reason: str | None,
        actor_user_id: UUID,
    ) -> FraudAssessment:
        row = await self.get(assess_id)
        if row is None:
            raise NotFoundError("Assessment not found.")
        row.decision = decision
        row.decision_reason = decision_reason
        row.decided_by = actor_user_id
        await self.session.flush()
        return row
