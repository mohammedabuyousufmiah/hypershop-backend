from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.finance.models import (
    Account,
    AccountingPeriod,
    CodDeposit,
    DailyClose,
    JournalEntry,
    JournalLine,
    RefundRecord,
    SupplierBill,
    SupplierPayment,
)


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(self, code: str) -> Account | None:
        return (
            await self.session.execute(
                select(Account).where(Account.code == code),
            )
        ).scalar_one_or_none()

    async def get(self, account_id: UUID) -> Account | None:
        return await self.session.get(Account, account_id)

    async def list_all(
        self, *, type: str | None = None, active_only: bool = False,
    ) -> Sequence[Account]:
        stmt = select(Account).order_by(Account.code)
        if type is not None:
            stmt = stmt.where(Account.type == type)
        if active_only:
            stmt = stmt.where(Account.is_active.is_(True))
        return (await self.session.execute(stmt)).scalars().all()

    async def upsert_system(
        self,
        *,
        code: str,
        name: str,
        type: str,
        description: str | None,
    ) -> Account:
        existing = await self.get_by_code(code)
        if existing is not None:
            existing.name = name
            existing.type = type
            existing.description = description
            existing.is_system = True
            await self.session.flush()
            return existing
        acc = Account(
            code=code,
            name=name,
            type=type,
            description=description,
            is_system=True,
            is_active=True,
        )
        self.session.add(acc)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError(f"Account code already exists: {code}") from e
        return acc

    async def update(self, account_id: UUID, **fields: Any) -> Account:
        acc = await self.get(account_id)
        if acc is None:
            raise NotFoundError("Account not found.")
        for k, v in fields.items():
            if v is not None:
                setattr(acc, k, v)
        await self.session.flush()
        return acc


class PeriodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, period_id: UUID) -> AccountingPeriod | None:
        return await self.session.get(AccountingPeriod, period_id)

    async def get_by_year_month(
        self, *, year: int, month: int,
    ) -> AccountingPeriod | None:
        return (
            await self.session.execute(
                select(AccountingPeriod).where(
                    AccountingPeriod.year == year,
                    AccountingPeriod.month == month,
                ),
            )
        ).scalar_one_or_none()

    async def upsert(
        self, *, year: int, month: int, starts_on: date, ends_on: date,
    ) -> AccountingPeriod:
        existing = await self.get_by_year_month(year=year, month=month)
        if existing is not None:
            return existing
        p = AccountingPeriod(
            year=year, month=month, starts_on=starts_on, ends_on=ends_on,
            status="open",
        )
        self.session.add(p)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Period already exists for that year/month.") from e
        return p

    async def list_all(self) -> Sequence[AccountingPeriod]:
        return (
            await self.session.execute(
                select(AccountingPeriod).order_by(
                    AccountingPeriod.year.desc(), AccountingPeriod.month.desc(),
                ),
            )
        ).scalars().all()


class JournalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entry_id: UUID) -> JournalEntry | None:
        return await self.session.get(JournalEntry, entry_id)

    async def get_by_code(self, code: str) -> JournalEntry | None:
        return (
            await self.session.execute(
                select(JournalEntry).where(JournalEntry.code == code),
            )
        ).scalar_one_or_none()

    async def add_entry(self, **fields: Any) -> JournalEntry:
        e = JournalEntry(**fields)
        self.session.add(e)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("Journal entry code conflict.") from exc
        return e

    async def add_line(self, **fields: Any) -> JournalLine:
        line = JournalLine(**fields)
        self.session.add(line)
        await self.session.flush()
        return line

    async def list_for_period(
        self,
        *,
        starts_on: date,
        ends_on: date,
        status: str | None = "posted",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[Sequence[JournalEntry], int]:
        conds: list[Any] = [
            JournalEntry.entry_date >= starts_on,
            JournalEntry.entry_date <= ends_on,
        ]
        if status is not None:
            conds.append(JournalEntry.status == status)
        count_stmt = (
            select(func.count()).select_from(JournalEntry).where(*conds)
        )
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        list_stmt = (
            select(JournalEntry)
            .where(*conds)
            .order_by(JournalEntry.entry_date.desc(), JournalEntry.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(list_stmt)).scalars().all()
        return rows, total

    async def list_for_reference(
        self, *, reference_type: str, reference_id: UUID,
    ) -> Sequence[JournalEntry]:
        stmt = (
            select(JournalEntry)
            .where(
                JournalEntry.reference_type == reference_type,
                JournalEntry.reference_id == reference_id,
            )
            .order_by(JournalEntry.entry_date, JournalEntry.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def has_entry_for_reference(
        self, *, reference_type: str, source: str, reference_id: UUID,
    ) -> bool:
        stmt = select(JournalEntry.id).where(
            JournalEntry.reference_type == reference_type,
            JournalEntry.reference_id == reference_id,
            JournalEntry.source == source,
            JournalEntry.status != "reversed",
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def account_totals(
        self, *, account_id: UUID, starts_on: date | None, ends_on: date,
    ) -> tuple[Decimal, Decimal]:
        """Sum (debits, credits) for an account across posted entries in range.

        ``starts_on`` is inclusive; ``ends_on`` is inclusive. ``None`` start
        means "from the beginning of time" (used by balance sheet).
        """
        conds: list[Any] = [
            JournalLine.account_id == account_id,
            JournalEntry.id == JournalLine.entry_id,
            JournalEntry.status == "posted",
            JournalEntry.entry_date <= ends_on,
        ]
        if starts_on is not None:
            conds.append(JournalEntry.entry_date >= starts_on)
        stmt = (
            select(
                func.coalesce(func.sum(JournalLine.debit), 0),
                func.coalesce(func.sum(JournalLine.credit), 0),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(and_(*conds))
        )
        row = (await self.session.execute(stmt)).one()
        return Decimal(row[0]), Decimal(row[1])

    async def daily_summary(
        self, *, day: date,
    ) -> dict[str, Any]:
        """Aggregate posted entries dated ``day``. Returns the totals used
        by the daily-close snapshot."""
        from app.modules.finance.accounts import (
            A_CASH_IN_BANK,
            A_CASH_ON_HAND_RIDER,
            C_COGS,
        )

        debit_credit_stmt = (
            select(
                func.coalesce(func.sum(JournalLine.debit), 0),
                func.coalesce(func.sum(JournalLine.credit), 0),
                func.count(func.distinct(JournalEntry.id)),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalEntry.status == "posted",
                JournalEntry.entry_date == day,
            )
        )
        d, c, n = (await self.session.execute(debit_credit_stmt)).one()

        # Revenue (credit balance, net of returns/discounts which are
        # debit-balanced contra-revenue accounts of type 'revenue').
        revenue_stmt = (
            select(
                func.coalesce(
                    func.sum(JournalLine.credit - JournalLine.debit),
                    0,
                ),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .join(Account, Account.id == JournalLine.account_id)
            .where(
                JournalEntry.status == "posted",
                JournalEntry.entry_date == day,
                Account.type == "revenue",
            )
        )
        revenue_total = Decimal(
            (await self.session.execute(revenue_stmt)).scalar_one() or 0,
        )

        cogs_stmt = (
            select(
                func.coalesce(
                    func.sum(JournalLine.debit - JournalLine.credit),
                    0,
                ),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalEntry.status == "posted",
                JournalEntry.entry_date == day,
                JournalLine.account_code == C_COGS,
            )
        )
        cogs_total = Decimal(
            (await self.session.execute(cogs_stmt)).scalar_one() or 0,
        )

        cash_codes = (A_CASH_IN_BANK, A_CASH_ON_HAND_RIDER)
        cash_in_stmt = (
            select(func.coalesce(func.sum(JournalLine.debit), 0))
            .select_from(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalEntry.status == "posted",
                JournalEntry.entry_date == day,
                JournalLine.account_code.in_(cash_codes),
            )
        )
        cash_out_stmt = (
            select(func.coalesce(func.sum(JournalLine.credit), 0))
            .select_from(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                JournalEntry.status == "posted",
                JournalEntry.entry_date == day,
                JournalLine.account_code.in_(cash_codes),
            )
        )
        cash_in = Decimal(
            (await self.session.execute(cash_in_stmt)).scalar_one() or 0,
        )
        cash_out = Decimal(
            (await self.session.execute(cash_out_stmt)).scalar_one() or 0,
        )

        return {
            "entry_count": int(n),
            "total_debits": Decimal(d),
            "total_credits": Decimal(c),
            "revenue_total": revenue_total,
            "cogs_total": cogs_total,
            "cash_in_total": cash_in,
            "cash_out_total": cash_out,
        }


class DailyCloseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_date(self, day: date) -> DailyClose | None:
        return (
            await self.session.execute(
                select(DailyClose).where(DailyClose.closing_date == day),
            )
        ).scalar_one_or_none()

    async def latest(self) -> DailyClose | None:
        return (
            await self.session.execute(
                select(DailyClose).order_by(DailyClose.closing_date.desc()).limit(1),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> DailyClose:
        c = DailyClose(**fields)
        self.session.add(c)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Daily close already exists for that date.") from e
        return c

    async def list_recent(self, *, limit: int = 30) -> Sequence[DailyClose]:
        return (
            await self.session.execute(
                select(DailyClose)
                .order_by(DailyClose.closing_date.desc())
                .limit(limit),
            )
        ).scalars().all()


class SupplierBillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, bill_id: UUID) -> SupplierBill | None:
        return await self.session.get(SupplierBill, bill_id)

    async def add(self, **fields: Any) -> SupplierBill:
        b = SupplierBill(**fields)
        self.session.add(b)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Supplier bill number already exists for this supplier.",
            ) from exc
        return b

    async def list_all(
        self,
        *,
        supplier_id: UUID | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[SupplierBill], int]:
        conds: list[Any] = []
        if supplier_id is not None:
            conds.append(SupplierBill.supplier_id == supplier_id)
        if status is not None:
            conds.append(SupplierBill.status == status)
        count_stmt = select(func.count()).select_from(SupplierBill)
        list_stmt = select(SupplierBill).order_by(
            SupplierBill.bill_date.desc(), SupplierBill.created_at.desc(),
        )
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        rows = (
            await self.session.execute(list_stmt.offset(offset).limit(limit))
        ).scalars().all()
        return rows, total

    async def add_payment(self, **fields: Any) -> SupplierPayment:
        p = SupplierPayment(**fields)
        self.session.add(p)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("Supplier payment code conflict.") from exc
        return p

    async def list_payments(
        self, *, bill_id: UUID,
    ) -> Sequence[SupplierPayment]:
        return (
            await self.session.execute(
                select(SupplierPayment)
                .where(SupplierPayment.bill_id == bill_id)
                .order_by(SupplierPayment.paid_on),
            )
        ).scalars().all()


class CodDepositRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, deposit_id: UUID) -> CodDeposit | None:
        return await self.session.get(CodDeposit, deposit_id)

    async def add(self, **fields: Any) -> CodDeposit:
        d = CodDeposit(**fields)
        self.session.add(d)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("COD deposit code conflict.") from exc
        return d

    async def list_for_rider(
        self, *, rider_id: UUID, limit: int = 50,
    ) -> Sequence[CodDeposit]:
        return (
            await self.session.execute(
                select(CodDeposit)
                .where(CodDeposit.rider_id == rider_id)
                .order_by(CodDeposit.deposit_date.desc())
                .limit(limit),
            )
        ).scalars().all()


class RefundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, refund_id: UUID) -> RefundRecord | None:
        return await self.session.get(RefundRecord, refund_id)

    async def get_by_return(self, return_request_id: UUID) -> RefundRecord | None:
        return (
            await self.session.execute(
                select(RefundRecord).where(
                    RefundRecord.return_request_id == return_request_id,
                ),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> RefundRecord:
        r = RefundRecord(**fields)
        self.session.add(r)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Refund record already exists for this return.",
            ) from exc
        return r

    async def list_all(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[RefundRecord], int]:
        conds: list[Any] = []
        if status is not None:
            conds.append(RefundRecord.status == status)
        count_stmt = select(func.count()).select_from(RefundRecord)
        list_stmt = select(RefundRecord).order_by(RefundRecord.created_at.desc())
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        rows = (
            await self.session.execute(list_stmt.offset(offset).limit(limit))
        ).scalars().all()
        return rows, total


def case_signed_balance(account_type_col: Any, debit_col: Any, credit_col: Any) -> Any:
    """SQL CASE producing a signed balance: positive on the account's normal side."""
    return case(
        (account_type_col.in_(("asset", "expense")), debit_col - credit_col),
        else_=credit_col - debit_col,
    )


def require_account(a: Account | None) -> Account:
    if a is None:
        raise NotFoundError("Account not found.")
    return a
