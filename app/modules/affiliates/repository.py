"""Affiliates DB layer."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.affiliates.models import AffiliateRecord


class AffiliateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user(self, user_id: UUID) -> AffiliateRecord | None:
        return (
            await self.session.execute(
                select(AffiliateRecord).where(
                    AffiliateRecord.user_id == user_id
                )
            )
        ).scalar_one_or_none()

    async def get_by_code(self, code: str) -> AffiliateRecord | None:
        return (
            await self.session.execute(
                select(AffiliateRecord).where(
                    func.lower(AffiliateRecord.code) == code.lower()
                )
            )
        ).scalar_one_or_none()

    async def insert(
        self,
        *,
        user_id: UUID,
        code: str,
        commission_rate,
    ) -> AffiliateRecord:
        row = AffiliateRecord(
            user_id=user_id, code=code, commission_rate=commission_rate
        )
        self.session.add(row)
        await self.session.flush()
        return row
