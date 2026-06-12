"""Loyalty repository — narrow DB layer."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.loyalty.models import LoyaltyAccount, LoyaltyTransaction


class LoyaltyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_account(self, user_id: UUID) -> LoyaltyAccount | None:
        return (
            await self.session.execute(
                select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def create_account(self, user_id: UUID) -> LoyaltyAccount:
        acc = LoyaltyAccount(user_id=user_id)
        self.session.add(acc)
        await self.session.flush()
        return acc

    async def append_txn(
        self,
        *,
        user_id: UUID,
        txn_type: str,
        points: int,
        source_order_id: UUID | None = None,
        expires_at: datetime | None = None,
        reason: str | None = None,
        created_by: UUID | None = None,
    ) -> LoyaltyTransaction:
        row = LoyaltyTransaction(
            user_id=user_id,
            txn_type=txn_type,
            points=points,
            source_order_id=source_order_id,
            expires_at=expires_at,
            reason=reason,
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_txns(
        self,
        user_id: UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[Sequence[LoyaltyTransaction], int]:
        base = select(LoyaltyTransaction).where(
            LoyaltyTransaction.user_id == user_id
        )
        items = (
            await self.session.execute(
                base.order_by(LoyaltyTransaction.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        total = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(LoyaltyTransaction)
                    .where(LoyaltyTransaction.user_id == user_id)
                )
            ).scalar_one()
        )
        return items, total

    async def txn_for_order(
        self,
        *,
        user_id: UUID,
        source_order_id: UUID,
        txn_type: str,
    ) -> LoyaltyTransaction | None:
        """Look up whether we already wrote a particular txn for this
        order — protects against double-earn / double-redeem when the
        order PAID signal fires twice."""
        return (
            await self.session.execute(
                select(LoyaltyTransaction).where(
                    LoyaltyTransaction.user_id == user_id,
                    LoyaltyTransaction.source_order_id == source_order_id,
                    LoyaltyTransaction.txn_type == txn_type,
                )
            )
        ).scalar_one_or_none()

    async def sum_earned_in_window(
        self,
        user_id: UUID,
        *,
        since: datetime,
    ) -> int:
        """Sum of EARN points for ``user_id`` with ``created_at >= since``.

        Drives the rolling-window tier compute (locked spec: tier ladder
        looks at points earned in the last 12 months, NOT the lifetime
        counter). REDEEM / EXPIRE / ADJUST rows are ignored — tier is
        about earn velocity, not net balance.
        """
        stmt = (
            select(func.coalesce(func.sum(LoyaltyTransaction.points), 0))
            .where(
                LoyaltyTransaction.user_id == user_id,
                LoyaltyTransaction.txn_type == "EARN",
                LoyaltyTransaction.created_at >= since,
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    @staticmethod
    def earn_expiry(now: datetime, *, days: int) -> datetime:
        return now + timedelta(days=days)

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def rolling_window_start(now: datetime, *, days: int) -> datetime:
        return now - timedelta(days=days)
