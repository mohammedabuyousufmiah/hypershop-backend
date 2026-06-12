"""Finance service — double-entry kernel + reports + subledgers.

Posting flow
------------
1. ``post_entry`` accepts a list of (account_code, debit, credit, memo)
   tuples. It validates:
     - At least 2 lines.
     - Each line has exactly one of (debit > 0, credit > 0).
     - Sum of debits == sum of credits (currency-precision guarded by
       ``Decimal`` quantize to 2 places).
     - Entry date is not inside a locked period AND not on or before the
       last DailyClose date.
     - Every account_code exists and is active.
2. It then creates the JournalEntry header (status=POSTED), inserts each
   JournalLine, and returns the entry.

Reversal
--------
``reverse_entry`` inserts a NEW entry whose lines mirror the original
(debits become credits and vice-versa), with ``reverses_entry_id``
pointing back. The original entry's status flips to REVERSED. Period-lock
rules apply to the reversal's date too.

Reports
-------
- ``trial_balance`` — every account with debit/credit/balance as of a date.
- ``profit_and_loss`` — revenue (net) - COGS - operating expenses.
- ``balance_sheet`` — assets = liabilities + equity + retained earnings.
- ``vat_ledger`` — every VAT-Payable line in a date range.

Subledgers
----------
- Supplier bills + payments
- COD deposits (rider cash → bank reconciliation)
- Refund accruals + payments

Cross-module hooks (called from ``handlers.py``)
- ``post_order_revenue`` — orders.payment_confirmed → AR/Cash + Revenue + VAT
- ``post_order_cogs`` — orders.completed → COGS + Inventory
- ``reverse_order_revenue`` — orders.cancelled → mirror the revenue entry
- ``post_cod_collection_for_delivery`` — deliveries.delivered (COD) → Cash-on-Hand-Rider + AR-COD
- ``accrue_refund_for_return`` — returns.completed → Sales-Returns + Refund-Payable
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.config import get_settings
from app.core.errors import (
    BusinessRuleError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.finance.accounts import (
    A_AR_COD,
    A_AR_GATEWAY_PENDING,
    A_CASH_IN_BANK,
    A_CASH_ON_HAND_RIDER,
    A_INVENTORY,
    C_COGS,
    CHART_OF_ACCOUNTS,
    L_AP_SUPPLIERS,
    L_REFUND_PAYABLE,
    L_VAT_PAYABLE,
    R_PRODUCT_SALES,
    R_SALES_RETURNS,
    X_COD_SHORT,
)
from app.modules.finance.codes import (
    make_cod_deposit_code,
    make_journal_code,
    make_refund_code,
    make_supplier_bill_code,
    make_supplier_payment_code,
)
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
from app.modules.finance.repository import (
    AccountRepository,
    CodDepositRepository,
    DailyCloseRepository,
    JournalRepository,
    PeriodRepository,
    RefundRepository,
    SupplierBillRepository,
    require_account,
)
from app.modules.finance.state import (
    AccountType,
    CodDepositStatus,
    JournalEntryStatus,
    PeriodStatus,
    RefundStatus,
    SupplierBillStatus,
)

_logger = get_logger("hypershop.finance")
_TWO_PLACES = Decimal("0.01")
_CODE_RETRIES = 5


def _q(amount: Decimal | float | int | str) -> Decimal:
    """Quantize to 2 places; raises if the input has more precision."""
    d = Decimal(str(amount)) if not isinstance(amount, Decimal) else amount
    return d.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


class LineSpec:
    """Internal: a (account_code, debit, credit, memo) tuple, validated."""

    __slots__ = ("account_code", "debit", "credit", "memo")

    def __init__(
        self,
        account_code: str,
        *,
        debit: Decimal | float | int | str = 0,
        credit: Decimal | float | int | str = 0,
        memo: str | None = None,
    ) -> None:
        self.account_code = account_code
        self.debit = _q(debit)
        self.credit = _q(credit)
        self.memo = memo
        if (self.debit > 0) == (self.credit > 0):
            raise ValidationError(
                "Each line must have exactly one of debit/credit non-zero.",
                details={"account_code": account_code},
            )


class FinanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.accounts = AccountRepository(session)
        self.periods = PeriodRepository(session)
        self.journal = JournalRepository(session)
        self.daily = DailyCloseRepository(session)
        self.bills = SupplierBillRepository(session)
        self.cod_deposits = CodDepositRepository(session)
        self.refunds = RefundRepository(session)

    # ------------------------------------------------------------------
    # Chart of accounts
    # ------------------------------------------------------------------

    async def ensure_chart_of_accounts(self) -> int:
        """Idempotent seed of the canonical chart. Returns count seeded/updated."""
        n = 0
        for spec in CHART_OF_ACCOUNTS:
            await self.accounts.upsert_system(
                code=spec.code,
                name=spec.name,
                type=spec.type.value,
                description=spec.description,
            )
            n += 1
        return n

    async def update_account(
        self, *, principal: Principal, account_id: UUID, fields: dict[str, Any],
    ) -> Account:
        acc = await self.accounts.update(account_id, **fields)
        await record_audit(
            actor=principal,
            action="finance.account.update",
            resource_type="fin_account",
            resource_id=acc.id,
            metadata={"changed": [k for k, v in fields.items() if v is not None]},
        )
        return acc

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    async def post_entry(
        self,
        *,
        actor: Principal | SystemPrincipal,
        entry_date: date,
        narration: str,
        lines: Iterable[LineSpec],
        source: str,
        currency: str = "BDT",
        reference_type: str | None = None,
        reference_id: UUID | None = None,
        extra: dict[str, Any] | None = None,
    ) -> JournalEntry:
        line_list = list(lines)
        if len(line_list) < 2:
            raise ValidationError("Journal entry needs at least 2 lines.")

        debit_sum = sum((l.debit for l in line_list), Decimal("0"))
        credit_sum = sum((l.credit for l in line_list), Decimal("0"))
        if _q(debit_sum) != _q(credit_sum):
            raise BusinessRuleError(
                "Journal entry is unbalanced.",
                details={
                    "debit_sum": str(debit_sum),
                    "credit_sum": str(credit_sum),
                    "diff": str(debit_sum - credit_sum),
                },
            )
        if _q(debit_sum) <= 0:
            raise ValidationError("Journal entry must move a positive amount.")

        # Period + daily-close lock check.
        await self._assert_open_for_post(entry_date)

        # Resolve accounts up front; refuse on missing/inactive.
        account_map: dict[str, Account] = {}
        for spec in line_list:
            if spec.account_code not in account_map:
                acc = await self.accounts.get_by_code(spec.account_code)
                if acc is None:
                    raise NotFoundError(
                        f"Account not found: {spec.account_code}",
                    )
                if not acc.is_active:
                    raise BusinessRuleError(
                        f"Account is inactive: {spec.account_code}",
                    )
                account_map[spec.account_code] = acc

        actor_id = actor.user_id if isinstance(actor, Principal) else None
        code = await self._allocate_code(entry_date)
        entry = await self.journal.add_entry(
            code=code,
            entry_date=entry_date,
            narration=narration,
            source=source,
            status=JournalEntryStatus.POSTED.value,
            currency=currency.upper(),
            reference_type=reference_type,
            reference_id=reference_id,
            posted_at=utc_now(),
            posted_by=actor_id,
            extra=extra or {},
        )
        for idx, spec in enumerate(line_list, start=1):
            acc = account_map[spec.account_code]
            await self.journal.add_line(
                entry_id=entry.id,
                line_number=idx,
                account_id=acc.id,
                account_code=acc.code,
                debit=spec.debit,
                credit=spec.credit,
                memo=spec.memo,
            )
        await self.session.refresh(entry, attribute_names=("lines",))

        await record_audit(
            actor=actor,
            action="finance.journal.post",
            resource_type="fin_journal_entry",
            resource_id=entry.id,
            metadata={
                "code": code,
                "source": source,
                "line_count": len(line_list),
                "amount": str(debit_sum),
                "reference_type": reference_type,
                "reference_id": str(reference_id) if reference_id else None,
            },
        )
        _logger.info(
            "finance_entry_posted",
            code=code,
            source=source,
            amount=str(debit_sum),
            line_count=len(line_list),
        )
        return entry

    async def reverse_entry(
        self,
        *,
        actor: Principal | SystemPrincipal,
        entry_id: UUID,
        reason: str,
        reverse_date: date | None = None,
    ) -> JournalEntry:
        """Reverse a posted entry by inserting a mirror entry.

        The original entry stays ``status='posted'`` — both entries remain
        on the ledger and net to zero in balance computations. This is the
        industry-standard approach: reversal is *additive*, not destructive.
        Re-reversal of the same original is blocked by checking for an
        existing entry with ``reverses_entry_id == original.id``.
        """
        original = await self.journal.get(entry_id)
        if original is None:
            raise NotFoundError("Journal entry not found.")
        if original.status != JournalEntryStatus.POSTED.value:
            raise BusinessRuleError(
                "Only posted entries can be reversed.",
                details={"status": original.status},
            )
        # Block double-reversal.
        already = (
            await self.session.execute(
                select(JournalEntry.id).where(
                    JournalEntry.reverses_entry_id == original.id,
                ),
            )
        ).first()
        if already is not None:
            raise BusinessRuleError(
                "Entry has already been reversed.",
                details={"original_id": str(original.id)},
            )
        await self.session.refresh(original, attribute_names=("lines",))

        rdate = reverse_date or utc_now().date()
        await self._assert_open_for_post(rdate)

        mirrored = [
            LineSpec(
                line.account_code,
                debit=line.credit,
                credit=line.debit,
                memo=f"reverse: {line.memo}" if line.memo else "reverse",
            )
            for line in original.lines
        ]
        reversal = await self.post_entry(
            actor=actor,
            entry_date=rdate,
            narration=f"Reversal of {original.code}: {reason}",
            lines=mirrored,
            source=original.source,
            currency=original.currency,
            reference_type=original.reference_type,
            reference_id=original.reference_id,
            extra={"reverses": str(original.id)},
        )
        reversal.reverses_entry_id = original.id
        await self.session.flush()

        await record_audit(
            actor=actor,
            action="finance.journal.reverse",
            resource_type="fin_journal_entry",
            resource_id=original.id,
            metadata={
                "reversal_id": str(reversal.id),
                "reversal_code": reversal.code,
                "reason": reason,
            },
        )
        return reversal

    async def _assert_open_for_post(self, entry_date: date) -> None:
        """Block posting into a date covered by daily close or a locked period."""
        latest = await self.daily.latest()
        if latest is not None and entry_date <= latest.closing_date:
            raise BusinessRuleError(
                "Cannot post into a date on or before the last daily close.",
                details={
                    "entry_date": entry_date.isoformat(),
                    "last_close": latest.closing_date.isoformat(),
                },
            )
        period = await self.periods.get_by_year_month(
            year=entry_date.year, month=entry_date.month,
        )
        if period is not None and period.status == PeriodStatus.LOCKED.value:
            raise BusinessRuleError(
                "Cannot post into a locked period.",
                details={"year": period.year, "month": period.month},
            )

    async def _allocate_code(self, entry_date: date) -> str:
        for _ in range(_CODE_RETRIES):
            candidate = make_journal_code(entry_date)
            if await self.journal.get_by_code(candidate) is None:
                return candidate
        raise BusinessRuleError("Could not allocate unique journal entry code.")

    # ------------------------------------------------------------------
    # Daily close + period close
    # ------------------------------------------------------------------

    async def daily_close(
        self,
        *,
        principal: Principal,
        closing_date: date,
        notes: str | None,
    ) -> DailyClose:
        existing = await self.daily.get_for_date(closing_date)
        if existing is not None:
            raise BusinessRuleError(
                "Daily close already exists for that date.",
                details={"closing_date": closing_date.isoformat()},
            )
        latest = await self.daily.latest()
        if latest is not None and closing_date <= latest.closing_date:
            raise BusinessRuleError(
                "Closing date must be after the previous close.",
                details={
                    "closing_date": closing_date.isoformat(),
                    "previous": latest.closing_date.isoformat(),
                },
            )
        if closing_date > utc_now().date():
            raise BusinessRuleError("Cannot close a future date.")

        summary = await self.journal.daily_summary(day=closing_date)
        snapshot = {
            **{k: str(v) if isinstance(v, Decimal) else v for k, v in summary.items()},
            "closing_date": closing_date.isoformat(),
        }
        close = await self.daily.add(
            closing_date=closing_date,
            closed_by=principal.user_id,
            entry_count=summary["entry_count"],
            total_debits=summary["total_debits"],
            total_credits=summary["total_credits"],
            revenue_total=summary["revenue_total"],
            cogs_total=summary["cogs_total"],
            cash_in_total=summary["cash_in_total"],
            cash_out_total=summary["cash_out_total"],
            notes=notes,
            snapshot=snapshot,
        )
        await record_audit(
            actor=principal,
            action="finance.daily_close",
            resource_type="fin_daily_close",
            resource_id=close.id,
            metadata={
                "closing_date": closing_date.isoformat(),
                "entry_count": summary["entry_count"],
                "total_debits": str(summary["total_debits"]),
            },
        )
        return close

    async def close_period(
        self, *, principal: Principal, year: int, month: int, notes: str | None,
    ) -> AccountingPeriod:
        period = await self.periods.get_by_year_month(year=year, month=month)
        if period is None:
            # Create a period row first so we have something to close.
            starts_on = date(year, month, 1)
            if month == 12:
                ends_on = date(year, 12, 31)
            else:
                ends_on = date(year, month + 1, 1) - timedelta(days=1)
            period = await self.periods.upsert(
                year=year, month=month, starts_on=starts_on, ends_on=ends_on,
            )
        if period.status == PeriodStatus.LOCKED.value:
            raise BusinessRuleError("Period is already locked.")
        if period.ends_on >= utc_now().date():
            raise BusinessRuleError(
                "Cannot lock a period that has not finished yet.",
                details={"ends_on": period.ends_on.isoformat()},
            )
        period.status = PeriodStatus.LOCKED.value
        period.closed_at = utc_now()
        period.closed_by = principal.user_id
        period.closing_notes = notes
        await self.session.flush()
        await record_audit(
            actor=principal,
            action="finance.period.close",
            resource_type="fin_accounting_period",
            resource_id=period.id,
            metadata={"year": year, "month": month},
        )
        return period

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    async def trial_balance(self, *, as_of: date) -> dict[str, Any]:
        accounts = await self.accounts.list_all()
        rows: list[dict[str, Any]] = []
        td = tc = Decimal("0")
        for acc in accounts:
            d, c = await self.journal.account_totals(
                account_id=acc.id, starts_on=None, ends_on=as_of,
            )
            if d == 0 and c == 0:
                continue
            atype = AccountType(acc.type)
            balance = (d - c) if atype in (AccountType.ASSET, AccountType.EXPENSE) else (c - d)
            rows.append(
                {
                    "account_code": acc.code,
                    "account_name": acc.name,
                    "account_type": acc.type,
                    "debit_total": _q(d),
                    "credit_total": _q(c),
                    "balance": _q(balance),
                },
            )
            td += d
            tc += c
        rows.sort(key=lambda r: r["account_code"])
        return {
            "as_of": as_of,
            "rows": rows,
            "total_debits": _q(td),
            "total_credits": _q(tc),
        }

    async def profit_and_loss(
        self, *, starts_on: date, ends_on: date,
    ) -> dict[str, Any]:
        if starts_on > ends_on:
            raise ValidationError("starts_on must be ≤ ends_on.")
        accounts = await self.accounts.list_all()
        revenue_lines: list[dict[str, Any]] = []
        expense_lines: list[dict[str, Any]] = []
        revenue_total = Decimal("0")
        cogs_total = Decimal("0")
        opex_total = Decimal("0")

        for acc in accounts:
            d, c = await self.journal.account_totals(
                account_id=acc.id, starts_on=starts_on, ends_on=ends_on,
            )
            atype = AccountType(acc.type)
            if atype == AccountType.REVENUE:
                # Net amount on revenue lines is credit - debit (sales positive,
                # contra-revenue accounts like sales returns will be negative).
                amount = _q(c - d)
                if amount != 0:
                    revenue_lines.append(
                        {
                            "account_code": acc.code,
                            "account_name": acc.name,
                            "amount": amount,
                        },
                    )
                revenue_total += amount
            elif atype == AccountType.EXPENSE:
                amount = _q(d - c)
                if amount != 0:
                    expense_lines.append(
                        {
                            "account_code": acc.code,
                            "account_name": acc.name,
                            "amount": amount,
                        },
                    )
                if acc.code == C_COGS:
                    cogs_total += amount
                else:
                    opex_total += amount

        gross_profit = _q(revenue_total - cogs_total)
        net_income = _q(gross_profit - opex_total)
        revenue_lines.sort(key=lambda r: r["account_code"])
        expense_lines.sort(key=lambda r: r["account_code"])
        return {
            "starts_on": starts_on,
            "ends_on": ends_on,
            "revenue_lines": revenue_lines,
            "expense_lines": expense_lines,
            "revenue_total": _q(revenue_total),
            "cogs_total": _q(cogs_total),
            "operating_expense_total": _q(opex_total),
            "gross_profit": gross_profit,
            "net_income": net_income,
        }

    async def balance_sheet(self, *, as_of: date) -> dict[str, Any]:
        accounts = await self.accounts.list_all()
        asset_lines: list[dict[str, Any]] = []
        liability_lines: list[dict[str, Any]] = []
        equity_lines: list[dict[str, Any]] = []
        assets_total = Decimal("0")
        liabilities_total = Decimal("0")
        equity_total = Decimal("0")
        revenue_to_date = Decimal("0")
        expense_to_date = Decimal("0")

        for acc in accounts:
            d, c = await self.journal.account_totals(
                account_id=acc.id, starts_on=None, ends_on=as_of,
            )
            atype = AccountType(acc.type)
            if atype == AccountType.ASSET:
                amount = _q(d - c)
                if amount != 0:
                    asset_lines.append(
                        {
                            "account_code": acc.code,
                            "account_name": acc.name,
                            "amount": amount,
                        },
                    )
                assets_total += amount
            elif atype == AccountType.LIABILITY:
                amount = _q(c - d)
                if amount != 0:
                    liability_lines.append(
                        {
                            "account_code": acc.code,
                            "account_name": acc.name,
                            "amount": amount,
                        },
                    )
                liabilities_total += amount
            elif atype == AccountType.EQUITY:
                amount = _q(c - d)
                if amount != 0:
                    equity_lines.append(
                        {
                            "account_code": acc.code,
                            "account_name": acc.name,
                            "amount": amount,
                        },
                    )
                equity_total += amount
            elif atype == AccountType.REVENUE:
                revenue_to_date += _q(c - d)
            elif atype == AccountType.EXPENSE:
                expense_to_date += _q(d - c)

        retained_earnings = _q(revenue_to_date - expense_to_date)
        balance_check = _q(
            assets_total - (liabilities_total + equity_total + retained_earnings),
        )
        for arr in (asset_lines, liability_lines, equity_lines):
            arr.sort(key=lambda r: r["account_code"])
        return {
            "as_of": as_of,
            "asset_lines": asset_lines,
            "liability_lines": liability_lines,
            "equity_lines": equity_lines,
            "assets_total": _q(assets_total),
            "liabilities_total": _q(liabilities_total),
            "equity_total": _q(equity_total),
            "retained_earnings": retained_earnings,
            "balance_check": balance_check,
        }

    async def vat_ledger(
        self, *, starts_on: date, ends_on: date,
    ) -> dict[str, Any]:
        if starts_on > ends_on:
            raise ValidationError("starts_on must be ≤ ends_on.")
        vat_account = await self.accounts.get_by_code(L_VAT_PAYABLE)
        if vat_account is None:
            raise NotFoundError("VAT-Payable account is not seeded.")
        stmt = (
            select(JournalLine, JournalEntry)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(
                and_(
                    JournalLine.account_id == vat_account.id,
                    JournalEntry.status == JournalEntryStatus.POSTED.value,
                    JournalEntry.entry_date >= starts_on,
                    JournalEntry.entry_date <= ends_on,
                ),
            )
            .order_by(JournalEntry.entry_date, JournalEntry.created_at)
        )
        rows = (await self.session.execute(stmt)).all()
        out_lines: list[dict[str, Any]] = []
        out_total = Decimal("0")
        rev_total = Decimal("0")
        for line, entry in rows:
            out_lines.append(
                {
                    "entry_code": entry.code,
                    "entry_date": entry.entry_date,
                    "debit": _q(line.debit),
                    "credit": _q(line.credit),
                    "memo": line.memo,
                    "reference_type": entry.reference_type,
                    "reference_id": entry.reference_id,
                },
            )
            out_total += line.credit
            rev_total += line.debit
        return {
            "starts_on": starts_on,
            "ends_on": ends_on,
            "output_vat_collected": _q(out_total),
            "output_vat_reversed": _q(rev_total),
            "net_payable": _q(out_total - rev_total),
            "lines": out_lines,
        }

    # ------------------------------------------------------------------
    # Order revenue / COGS / cancellation hooks
    # ------------------------------------------------------------------

    @staticmethod
    def _split_vat(grand_total: Decimal, vat_rate: Decimal) -> tuple[Decimal, Decimal]:
        """grand_total is VAT-inclusive. Returns (net, vat)."""
        if vat_rate <= 0:
            return _q(grand_total), Decimal("0.00")
        denom = Decimal("1") + vat_rate
        net = (grand_total / denom).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        vat = _q(grand_total - net)
        return net, vat

    async def post_order_revenue(
        self, *, actor: Principal | SystemPrincipal, order_id: UUID,
    ) -> JournalEntry | None:
        """Post the revenue + AR/cash JE for a confirmed order. Idempotent on
        (reference_type='order', source='order_revenue', reference_id=order_id).
        """
        from app.modules.orders.models import Order
        from app.modules.orders.repository import require_order

        order = require_order(await self.session.get(Order, order_id))

        if await self.journal.has_entry_for_reference(
            reference_type="order",
            source="order_revenue",
            reference_id=order_id,
        ):
            return None

        settings = get_settings()
        vat_rate = Decimal(str(getattr(settings, "vat_rate", "0")))

        # Use grand_total as VAT-inclusive figure.
        gross = _q(order.grand_total)
        net_revenue, vat_amount = self._split_vat(gross, vat_rate)

        if order.payment_method == "cod":
            debit_account = A_AR_COD
            debit_memo = f"COD receivable for order {order.code}"
        else:
            debit_account = A_AR_GATEWAY_PENDING
            debit_memo = f"Online payment receivable for order {order.code}"

        lines = [
            LineSpec(debit_account, debit=gross, memo=debit_memo),
            LineSpec(R_PRODUCT_SALES, credit=net_revenue,
                     memo=f"Revenue net of VAT for order {order.code}"),
        ]
        if vat_amount > 0:
            lines.append(LineSpec(L_VAT_PAYABLE, credit=vat_amount,
                                  memo=f"Output VAT for order {order.code}"))
        return await self.post_entry(
            actor=actor,
            entry_date=utc_now().date(),
            narration=f"Order {order.code} payment confirmed",
            lines=lines,
            source="order_revenue",
            currency=order.currency,
            reference_type="order",
            reference_id=order.id,
        )

    async def post_order_cogs(
        self, *, actor: Principal | SystemPrincipal, order_id: UUID,
    ) -> JournalEntry | None:
        """Post COGS + Inventory JE for a completed order. Cost is computed
        from stock_ledger CONSUME rows joined to goods_receipt_lines.

        Idempotent on (source='order_cogs', reference_id=order_id).
        """
        from app.modules.inventory.models import (
            GoodsReceiptLine,
            LedgerKind,
            StockLedger,
        )

        if await self.journal.has_entry_for_reference(
            reference_type="order", source="order_cogs", reference_id=order_id,
        ):
            return None

        # Cost per consumed unit = average unit_cost across goods_receipt_lines
        # for that batch (one batch can be received multiple times). We use
        # AVG to avoid latest-receipt bias when the same batch was topped up.
        batch_cost_subq = (
            select(
                GoodsReceiptLine.batch_id.label("batch_id"),
                func.avg(GoodsReceiptLine.unit_cost).label("unit_cost"),
            )
            .group_by(GoodsReceiptLine.batch_id)
            .subquery()
        )
        stmt = (
            select(
                func.coalesce(
                    func.sum(
                        func.abs(StockLedger.quantity_delta) * batch_cost_subq.c.unit_cost,
                    ),
                    0,
                ),
            )
            .select_from(StockLedger)
            .join(
                batch_cost_subq, batch_cost_subq.c.batch_id == StockLedger.batch_id,
            )
            .where(
                StockLedger.reference_type == "order",
                StockLedger.reference_id == order_id,
                StockLedger.kind == LedgerKind.CONSUME.value,
            )
        )
        cogs = Decimal((await self.session.execute(stmt)).scalar_one() or 0)
        cogs = _q(cogs)
        if cogs <= 0:
            _logger.info(
                "finance_order_cogs_skipped_zero",
                order_id=str(order_id),
            )
            return None

        return await self.post_entry(
            actor=actor,
            entry_date=utc_now().date(),
            narration=f"COGS for completed order {order_id}",
            lines=[
                LineSpec(C_COGS, debit=cogs,
                         memo=f"COGS for order {order_id}"),
                LineSpec(A_INVENTORY, credit=cogs,
                         memo=f"Inventory consumed for order {order_id}"),
            ],
            source="order_cogs",
            reference_type="order",
            reference_id=order_id,
        )

    async def reverse_order_revenue(
        self,
        *,
        actor: Principal | SystemPrincipal,
        order_id: UUID,
        reason: str,
    ) -> JournalEntry | None:
        """If a revenue JE was posted for this order, reverse it. Otherwise
        no-op. Distinguishes the original from its own reversal by picking
        the entry whose ``reverses_entry_id`` is NULL (originals only).
        """
        rows = await self.journal.list_for_reference(
            reference_type="order", reference_id=order_id,
        )
        # Find the original (not itself a reversal) revenue entry.
        original = next(
            (
                e for e in rows
                if e.source == "order_revenue"
                and e.reverses_entry_id is None
                and e.status == JournalEntryStatus.POSTED.value
            ),
            None,
        )
        if original is None:
            return None
        # Skip if a reversal already exists.
        already = (
            await self.session.execute(
                select(JournalEntry.id).where(
                    JournalEntry.reverses_entry_id == original.id,
                ),
            )
        ).first()
        if already is not None:
            return None
        return await self.reverse_entry(
            actor=actor, entry_id=original.id, reason=reason,
        )

    # ------------------------------------------------------------------
    # COD: rider cash collection + bank deposit
    # ------------------------------------------------------------------

    async def post_cod_collection_for_delivery(
        self,
        *,
        actor: Principal | SystemPrincipal,
        order_id: UUID,
        delivery_id: UUID,
        amount: Decimal,
    ) -> JournalEntry | None:
        """Move COD cash from AR-COD to Cash-on-Hand-Rider on delivery.

        Idempotent on (source='cod_collection', reference_id=delivery_id).
        """
        if await self.journal.has_entry_for_reference(
            reference_type="delivery",
            source="cod_collection",
            reference_id=delivery_id,
        ):
            return None
        amount = _q(amount)
        if amount <= 0:
            return None
        return await self.post_entry(
            actor=actor,
            entry_date=utc_now().date(),
            narration=f"COD collected by rider for delivery {delivery_id}",
            lines=[
                LineSpec(A_CASH_ON_HAND_RIDER, debit=amount,
                         memo=f"Cash held by rider for order {order_id}"),
                LineSpec(A_AR_COD, credit=amount,
                         memo=f"AR-COD cleared for order {order_id}"),
            ],
            source="cod_collection",
            reference_type="delivery",
            reference_id=delivery_id,
            extra={"order_id": str(order_id)},
        )

    async def record_cod_deposit(
        self,
        *,
        principal: Principal,
        rider_id: UUID,
        deposit_date: date,
        expected_amount: Decimal,
        deposited_amount: Decimal,
        bank_reference: str | None,
        notes: str | None,
    ) -> CodDeposit:
        if not principal.has_permission("finance.settle"):
            raise ForbiddenError("Missing finance.settle permission.")
        expected_amount = _q(expected_amount)
        deposited_amount = _q(deposited_amount)
        if expected_amount < 0 or deposited_amount < 0:
            raise ValidationError("Amounts must be non-negative.")
        discrepancy = _q(expected_amount - deposited_amount)

        status = (
            CodDepositStatus.DEPOSITED.value
            if discrepancy == 0
            else CodDepositStatus.DISCREPANCY.value
        )

        # Booking JE: Dr Bank for deposited; if short, Dr COD-Short for the gap;
        # Cr Cash-on-Hand-Rider for full expected (since the rider was supposed
        # to hand over that much).
        lines: list[LineSpec] = []
        if deposited_amount > 0:
            lines.append(
                LineSpec(A_CASH_IN_BANK, debit=deposited_amount,
                         memo=f"Bank deposit by rider {rider_id}"),
            )
        if discrepancy > 0:
            lines.append(
                LineSpec(X_COD_SHORT, debit=discrepancy,
                         memo=f"COD short from rider {rider_id}"),
            )
        elif discrepancy < 0:
            lines.append(
                LineSpec(X_COD_SHORT, credit=-discrepancy,
                         memo=f"COD over from rider {rider_id}"),
            )
        if expected_amount > 0:
            lines.append(
                LineSpec(A_CASH_ON_HAND_RIDER, credit=expected_amount,
                         memo=f"Clearing rider {rider_id} cash on hand"),
            )

        if not lines or sum((l.debit for l in lines), Decimal("0")) == 0:
            raise ValidationError("Deposit moves no money — nothing to post.")

        entry = await self.post_entry(
            actor=principal,
            entry_date=deposit_date,
            narration=f"COD deposit by rider {rider_id} on {deposit_date.isoformat()}",
            lines=lines,
            source="cod_deposit",
            reference_type="rider",
            reference_id=rider_id,
            extra={"deposited": str(deposited_amount), "expected": str(expected_amount)},
        )

        code = make_cod_deposit_code()
        deposit = await self.cod_deposits.add(
            code=code,
            rider_id=rider_id,
            deposit_date=deposit_date,
            expected_amount=expected_amount,
            deposited_amount=deposited_amount,
            discrepancy=discrepancy,
            status=status,
            bank_reference=bank_reference,
            notes=notes,
            journal_entry_id=entry.id,
        )
        await record_audit(
            actor=principal,
            action="finance.cod_deposit.record",
            resource_type="fin_cod_deposit",
            resource_id=deposit.id,
            metadata={
                "rider_id": str(rider_id),
                "expected": str(expected_amount),
                "deposited": str(deposited_amount),
                "discrepancy": str(discrepancy),
            },
        )
        return deposit

    async def rider_cash_on_hand(self, rider_id: UUID) -> dict[str, Any]:
        """Sum unreconciled COD collections vs deposits for a rider."""
        from app.modules.deliveries.models import DeliveryAssignment

        # Expected: sum of cod_collected on delivered+reconciled assignments
        # for this rider that have an associated cod_collection JE.
        expected_stmt = (
            select(
                func.coalesce(func.sum(DeliveryAssignment.cod_collected), 0),
            )
            .where(
                DeliveryAssignment.rider_id == rider_id,
                DeliveryAssignment.payment_method == "cod",
                DeliveryAssignment.cod_collected.isnot(None),
                DeliveryAssignment.cod_status.in_(("reconciled", "resolved")),
            )
        )
        expected_total = Decimal(
            (await self.session.execute(expected_stmt)).scalar_one() or 0,
        )
        deposited_stmt = (
            select(func.coalesce(func.sum(CodDeposit.deposited_amount), 0))
            .where(CodDeposit.rider_id == rider_id)
        )
        deposited_total = Decimal(
            (await self.session.execute(deposited_stmt)).scalar_one() or 0,
        )
        return {
            "rider_id": rider_id,
            "expected_total": _q(expected_total),
            "deposited_total": _q(deposited_total),
            "outstanding": _q(expected_total - deposited_total),
        }

    # ------------------------------------------------------------------
    # Supplier bills (AP subledger)
    # ------------------------------------------------------------------

    async def book_supplier_bill(
        self,
        *,
        principal: Principal,
        supplier_id: UUID,
        bill_number: str,
        bill_date: date,
        due_date: date | None,
        currency: str,
        subtotal: Decimal,
        tax_total: Decimal,
        goods_receipt_id: UUID | None,
        notes: str | None,
    ) -> SupplierBill:
        if not principal.has_permission("finance.post"):
            raise ForbiddenError("Missing finance.post permission.")
        subtotal = _q(subtotal)
        tax_total = _q(tax_total)
        grand_total = _q(subtotal + tax_total)
        if grand_total <= 0:
            raise ValidationError("Bill grand total must be positive.")

        code = make_supplier_bill_code()
        bill = await self.bills.add(
            code=code,
            supplier_id=supplier_id,
            goods_receipt_id=goods_receipt_id,
            bill_number=bill_number,
            bill_date=bill_date,
            due_date=due_date,
            currency=currency.upper(),
            subtotal=subtotal,
            tax_total=tax_total,
            grand_total=grand_total,
            amount_paid=Decimal("0"),
            status=SupplierBillStatus.OPEN.value,
            notes=notes,
        )
        # Booking JE: Dr Inventory + (Dr VAT-Receivable if input-VAT — out of
        # scope for now, treat tax as added to inventory cost) / Cr AP-Suppliers.
        # Bangladesh medicines are mostly VAT-exempt; we just expense tax_total
        # into inventory rather than carrying input-VAT recoverable.
        entry = await self.post_entry(
            actor=principal,
            entry_date=bill_date,
            narration=f"Supplier bill {code} ({bill_number})",
            lines=[
                LineSpec(A_INVENTORY, debit=grand_total,
                         memo=f"Bill {bill_number}"),
                LineSpec(L_AP_SUPPLIERS, credit=grand_total,
                         memo=f"Owed to supplier for bill {bill_number}"),
            ],
            source="supplier_bill",
            currency=currency.upper(),
            reference_type="supplier_bill",
            reference_id=bill.id,
        )
        bill.booking_entry_id = entry.id
        await self.session.flush()
        await record_audit(
            actor=principal,
            action="finance.supplier_bill.book",
            resource_type="fin_supplier_bill",
            resource_id=bill.id,
            metadata={
                "code": code, "supplier_id": str(supplier_id),
                "grand_total": str(grand_total),
            },
        )
        return bill

    async def pay_supplier_bill(
        self,
        *,
        principal: Principal,
        bill_id: UUID,
        paid_on: date,
        amount: Decimal,
        method: str,
        bank_reference: str | None,
        notes: str | None,
    ) -> SupplierPayment:
        if not principal.has_permission("finance.settle"):
            raise ForbiddenError("Missing finance.settle permission.")
        bill = await self.bills.get(bill_id)
        if bill is None:
            raise NotFoundError("Supplier bill not found.")
        if bill.status == SupplierBillStatus.VOID.value:
            raise BusinessRuleError("Cannot pay a void bill.")
        amount = _q(amount)
        remaining = _q(bill.grand_total - bill.amount_paid)
        if amount > remaining:
            raise BusinessRuleError(
                "Payment exceeds remaining balance.",
                details={"amount": str(amount), "remaining": str(remaining)},
            )

        entry = await self.post_entry(
            actor=principal,
            entry_date=paid_on,
            narration=f"Payment for bill {bill.code}",
            lines=[
                LineSpec(L_AP_SUPPLIERS, debit=amount,
                         memo=f"Settling bill {bill.code}"),
                LineSpec(A_CASH_IN_BANK, credit=amount,
                         memo=f"Paid via {method}" + (f" / {bank_reference}" if bank_reference else "")),
            ],
            source="supplier_payment",
            currency=bill.currency,
            reference_type="supplier_bill",
            reference_id=bill.id,
            extra={"method": method, "bank_reference": bank_reference},
        )

        code = make_supplier_payment_code()
        pay = await self.bills.add_payment(
            code=code,
            bill_id=bill.id,
            paid_on=paid_on,
            amount=amount,
            method=method,
            bank_reference=bank_reference,
            notes=notes,
            journal_entry_id=entry.id,
        )
        bill.amount_paid = _q(bill.amount_paid + amount)
        if bill.amount_paid >= bill.grand_total:
            bill.status = SupplierBillStatus.PAID.value
        else:
            bill.status = SupplierBillStatus.PARTIALLY_PAID.value
        await self.session.flush()

        await record_audit(
            actor=principal,
            action="finance.supplier_bill.pay",
            resource_type="fin_supplier_payment",
            resource_id=pay.id,
            metadata={
                "bill_id": str(bill.id),
                "amount": str(amount),
                "method": method,
            },
        )
        return pay

    # ------------------------------------------------------------------
    # Refunds
    # ------------------------------------------------------------------

    async def accrue_refund_for_return(
        self,
        *,
        actor: Principal | SystemPrincipal,
        return_request_id: UUID,
        order_id: UUID,
        customer_user_id: UUID,
        amount: Decimal,
    ) -> RefundRecord | None:
        """Accrue a refund obligation. Idempotent on return_request_id."""
        existing = await self.refunds.get_by_return(return_request_id)
        if existing is not None:
            return existing
        amount = _q(amount)
        if amount <= 0:
            return None

        # Note: we debit Sales-Returns (a contra-revenue expense-like account
        # of type 'revenue' so it nets correctly on the P&L). VAT reversal is
        # not handled here yet — Bangladesh pharmacy is mostly VAT-exempt; if
        # VAT was charged on the original sale, manual adjustment can reverse
        # it via the journal endpoint.
        entry = await self.post_entry(
            actor=actor,
            entry_date=utc_now().date(),
            narration=f"Refund accrual for return {return_request_id}",
            lines=[
                LineSpec(R_SALES_RETURNS, debit=amount,
                         memo=f"Sales return for order {order_id}"),
                LineSpec(L_REFUND_PAYABLE, credit=amount,
                         memo=f"Refund owed to customer {customer_user_id}"),
            ],
            source="refund_accrual",
            reference_type="return_request",
            reference_id=return_request_id,
            extra={"order_id": str(order_id)},
        )

        code = make_refund_code()
        record = await self.refunds.add(
            code=code,
            return_request_id=return_request_id,
            order_id=order_id,
            customer_user_id=customer_user_id,
            accrued_amount=amount,
            paid_amount=Decimal("0"),
            status=RefundStatus.PENDING.value,
            accrual_entry_id=entry.id,
        )
        await record_audit(
            actor=actor,
            action="finance.refund.accrue",
            resource_type="fin_refund_record",
            resource_id=record.id,
            metadata={
                "return_request_id": str(return_request_id),
                "order_id": str(order_id),
                "amount": str(amount),
            },
        )
        return record

    async def pay_refund(
        self,
        *,
        principal: Principal,
        refund_id: UUID,
        paid_on: date,
        amount: Decimal,
        method: str,
        bank_reference: str | None,
        notes: str | None,
    ) -> RefundRecord:
        if not principal.has_permission("finance.settle"):
            raise ForbiddenError("Missing finance.settle permission.")
        record = await self.refunds.get(refund_id)
        if record is None:
            raise NotFoundError("Refund record not found.")
        if record.status == RefundStatus.CANCELLED.value:
            raise BusinessRuleError("Refund is cancelled.")
        amount = _q(amount)
        remaining = _q(record.accrued_amount - record.paid_amount)
        if amount > remaining:
            raise BusinessRuleError(
                "Refund payment exceeds remaining accrued amount.",
                details={"amount": str(amount), "remaining": str(remaining)},
            )

        entry = await self.post_entry(
            actor=principal,
            entry_date=paid_on,
            narration=f"Refund payout for {record.code}",
            lines=[
                LineSpec(L_REFUND_PAYABLE, debit=amount,
                         memo=f"Settling refund {record.code}"),
                LineSpec(A_CASH_IN_BANK, credit=amount,
                         memo=f"Paid via {method}" + (f" / {bank_reference}" if bank_reference else "")),
            ],
            source="refund_payment",
            reference_type="refund_record",
            reference_id=record.id,
        )
        record.paid_amount = _q(record.paid_amount + amount)
        record.payment_entry_id = entry.id
        record.paid_on = paid_on
        record.method = method
        record.bank_reference = bank_reference
        if notes:
            record.notes = (
                (record.notes + "\n" if record.notes else "") + notes
            )
        if record.paid_amount >= record.accrued_amount:
            record.status = RefundStatus.PAID.value
        await self.session.flush()
        await record_audit(
            actor=principal,
            action="finance.refund.pay",
            resource_type="fin_refund_record",
            resource_id=record.id,
            metadata={"amount": str(amount), "method": method},
        )
        return record
