"""SQLA wrappers for rider_wallet tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rider_wallet.models import (
    RiderCashLimit,
    RiderSettlement,
    RiderWallet,
    RiderWalletDailySummary,
    RiderWalletLedger,
)


# ============================================================
#  RiderWalletRepository
# ============================================================
class RiderWalletRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_rider(self, rider_id: UUID) -> RiderWallet | None:
        return (
            await self.session.execute(
                select(RiderWallet).where(RiderWallet.rider_id == rider_id),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> RiderWallet:
        row = RiderWallet(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(
        self, *,
        wallet_id: UUID,
        **fields: Any,
    ) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(RiderWallet)
            .where(RiderWallet.id == wallet_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)

    async def list_blocked(
        self, *, limit: int = 200,
    ) -> Sequence[RiderWallet]:
        return (
            (
                await self.session.execute(
                    select(RiderWallet)
                    .where(RiderWallet.assignment_locked.is_(True))
                    .order_by(desc(RiderWallet.overdue_since))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def list_all(
        self, *, limit: int = 200,
    ) -> Sequence[RiderWallet]:
        return (
            (
                await self.session.execute(
                    select(RiderWallet)
                    .order_by(desc(RiderWallet.wallet_payable_to_company))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def find_riders_with_unpaid(
        self,
    ) -> list[UUID]:
        """For the nightly sweep: every wallet with payable > 0 that's
        NOT already locked AND NOT covered by an active carry-forward
        AND NOT covered by a pending-settlement that totals to the
        payable amount (rider already did their part — finance
        hasn't verified yet, but locking them would be unfair).
        """
        from app.core.time import utc_now
        now = utc_now()
        rows = (
            (
                await self.session.execute(
                    select(RiderWallet.rider_id)
                    .where(
                        and_(
                            RiderWallet.wallet_payable_to_company > 0,
                            RiderWallet.assignment_locked.is_(False),
                            # Rider hasn't submitted a covering settlement.
                            (
                                RiderWallet.wallet_pending_settlement <
                                RiderWallet.wallet_payable_to_company
                            ),
                            # carry-forward NOT in effect
                            ~(
                                and_(
                                    RiderWallet.carry_forward_approved.is_(True),
                                    RiderWallet.carry_forward_expires_at.isnot(None),
                                    RiderWallet.carry_forward_expires_at > now,
                                )
                            ),
                        ),
                    ),
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


# ============================================================
#  RiderWalletLedgerRepository
# ============================================================
class RiderWalletLedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> RiderWalletLedger:
        row = RiderWalletLedger(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_rider(
        self,
        rider_id: UUID,
        *,
        limit: int = 200,
        before: datetime | None = None,
    ) -> Sequence[RiderWalletLedger]:
        stmt = (
            select(RiderWalletLedger)
            .where(RiderWalletLedger.rider_id == rider_id)
        )
        if before is not None:
            stmt = stmt.where(RiderWalletLedger.created_at < before)
        stmt = stmt.order_by(desc(RiderWalletLedger.created_at)).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def has_assignment_entry(
        self, *,
        delivery_assignment_id: UUID,
        entry_type: str,
    ) -> bool:
        """Idempotency check — has a cod_collection row already been
        posted for this assignment? Lets the COD-reconciled handler
        be safely retried.
        """
        result = (
            await self.session.execute(
                select(func.count(RiderWalletLedger.id))
                .where(
                    and_(
                        RiderWalletLedger.delivery_assignment_id ==
                        delivery_assignment_id,
                        RiderWalletLedger.entry_type == entry_type,
                    ),
                ),
            )
        ).scalar_one()
        return int(result or 0) > 0

    async def sum_for_rider_in_range(
        self,
        rider_id: UUID,
        *,
        starts_on: date,
        ends_on: date,
        entry_type: str,
    ) -> Decimal:
        from sqlalchemy import cast, Date as SaDate
        result = (
            await self.session.execute(
                select(func.coalesce(func.sum(RiderWalletLedger.amount), 0))
                .where(
                    and_(
                        RiderWalletLedger.rider_id == rider_id,
                        RiderWalletLedger.entry_type == entry_type,
                        cast(RiderWalletLedger.created_at, SaDate) >= starts_on,
                        cast(RiderWalletLedger.created_at, SaDate) <= ends_on,
                    ),
                ),
            )
        ).scalar_one()
        return Decimal(result or 0)


# ============================================================
#  RiderSettlementRepository
# ============================================================
class RiderSettlementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, settlement_id: UUID) -> RiderSettlement | None:
        return await self.session.get(RiderSettlement, settlement_id)

    async def get_by_transaction_id(
        self, transaction_id: str,
    ) -> RiderSettlement | None:
        return (
            await self.session.execute(
                select(RiderSettlement)
                .where(RiderSettlement.transaction_id == transaction_id),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> RiderSettlement:
        row = RiderSettlement(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(
        self, *,
        settlement_id: UUID,
        **fields: Any,
    ) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(RiderSettlement)
            .where(RiderSettlement.id == settlement_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)

    async def list_for_rider(
        self,
        rider_id: UUID,
        *,
        statuses: Sequence[str] | None = None,
        limit: int = 100,
    ) -> Sequence[RiderSettlement]:
        stmt = (
            select(RiderSettlement)
            .where(RiderSettlement.rider_id == rider_id)
        )
        if statuses:
            stmt = stmt.where(RiderSettlement.status.in_(list(statuses)))
        stmt = stmt.order_by(desc(RiderSettlement.created_at)).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_pending(
        self, *, limit: int = 200,
    ) -> Sequence[RiderSettlement]:
        return (
            (
                await self.session.execute(
                    select(RiderSettlement)
                    .where(RiderSettlement.status.in_(["submitted", "under_review"]))
                    .order_by(RiderSettlement.created_at)
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )


# ============================================================
#  RiderCashLimitRepository
# ============================================================
class RiderCashLimitRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_rider(self, rider_id: UUID) -> RiderCashLimit | None:
        return (
            await self.session.execute(
                select(RiderCashLimit).where(
                    RiderCashLimit.rider_id == rider_id,
                ),
            )
        ).scalar_one_or_none()

    async def upsert(
        self, *,
        rider_id: UUID,
        max_cash_in_hand: Decimal,
        max_unsettled_amount: Decimal,
        allow_carry_forward: bool,
        carry_forward_limit: Decimal,
    ) -> RiderCashLimit:
        existing = await self.get_by_rider(rider_id)
        if existing is not None:
            await self.session.execute(
                update(RiderCashLimit)
                .where(RiderCashLimit.id == existing.id)
                .values(
                    max_cash_in_hand=max_cash_in_hand,
                    max_unsettled_amount=max_unsettled_amount,
                    allow_carry_forward=allow_carry_forward,
                    carry_forward_limit=carry_forward_limit,
                ),
            )
            return await self.get_by_rider(rider_id)  # type: ignore[return-value]
        row = RiderCashLimit(
            rider_id=rider_id,
            max_cash_in_hand=max_cash_in_hand,
            max_unsettled_amount=max_unsettled_amount,
            allow_carry_forward=allow_carry_forward,
            carry_forward_limit=carry_forward_limit,
        )
        self.session.add(row)
        await self.session.flush()
        return row


# ============================================================
#  RiderWalletDailySummaryRepository
# ============================================================
class RiderWalletDailySummaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_date(
        self, *, rider_id: UUID, summary_date: date,
    ) -> RiderWalletDailySummary | None:
        return (
            await self.session.execute(
                select(RiderWalletDailySummary)
                .where(
                    and_(
                        RiderWalletDailySummary.rider_id == rider_id,
                        RiderWalletDailySummary.summary_date == summary_date,
                    ),
                ),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> RiderWalletDailySummary:
        row = RiderWalletDailySummary(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_recent(
        self, rider_id: UUID, *, limit: int = 30,
    ) -> Sequence[RiderWalletDailySummary]:
        return (
            (
                await self.session.execute(
                    select(RiderWalletDailySummary)
                    .where(RiderWalletDailySummary.rider_id == rider_id)
                    .order_by(desc(RiderWalletDailySummary.summary_date))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )
