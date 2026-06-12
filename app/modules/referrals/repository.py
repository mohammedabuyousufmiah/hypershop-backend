"""Referrals repository — narrow DB layer."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.referrals.models import ReferralAttribution, ReferralCode


class ReferralRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_code_by_user(self, user_id: UUID) -> ReferralCode | None:
        return (
            await self.session.execute(
                select(ReferralCode).where(ReferralCode.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def get_code_by_text(self, code: str) -> ReferralCode | None:
        return (
            await self.session.execute(
                select(ReferralCode).where(func.lower(ReferralCode.code) == code.lower())
            )
        ).scalar_one_or_none()

    async def insert_code(self, user_id: UUID, code: str) -> ReferralCode:
        row = ReferralCode(user_id=user_id, code=code)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_attribution_for_referee(
        self, referee_user_id: UUID
    ) -> ReferralAttribution | None:
        return (
            await self.session.execute(
                select(ReferralAttribution).where(
                    ReferralAttribution.referee_user_id == referee_user_id
                )
            )
        ).scalar_one_or_none()

    async def insert_attribution(
        self,
        *,
        referrer_user_id: UUID,
        referee_user_id: UUID,
    ) -> ReferralAttribution:
        row = ReferralAttribution(
            referrer_user_id=referrer_user_id,
            referee_user_id=referee_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def count_pending(self, referrer_user_id: UUID) -> int:
        return int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(ReferralAttribution)
                    .where(
                        ReferralAttribution.referrer_user_id
                        == referrer_user_id,
                        ReferralAttribution.status == "PENDING",
                    )
                )
            ).scalar_one()
        )
