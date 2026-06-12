from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ValidationError
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.finance.repository import (
    AccountRepository,
    CodDepositRepository,
    DailyCloseRepository,
    JournalRepository,
    PeriodRepository,
    RefundRepository,
    SupplierBillRepository,
)
from app.modules.finance.schemas import (
    AccountResponse,
    AccountUpdate,
    BalanceSheetReport,
    CodDepositCreate,
    CodDepositResponse,
    DailyCloseRequest,
    DailyCloseResponse,
    JournalEntryCreate,
    JournalEntryResponse,
    JournalEntryReverseRequest,
    JournalLineResponse,
    PeriodCloseRequest,
    PeriodResponse,
    PnLReport,
    RefundPaymentRequest,
    RefundRecordResponse,
    RiderCashOnHandResponse,
    SupplierBillCreate,
    SupplierBillResponse,
    SupplierPaymentCreate,
    SupplierPaymentResponse,
    TrialBalanceResponse,
    VatLedgerResponse,
)
from app.modules.finance.service import FinanceService, LineSpec

router = APIRouter(prefix="/admin/finance", tags=["admin-finance"])

_READ = "finance.read"
_POST = "finance.post"
_SETTLE = "finance.settle"
_CLOSE = "finance.close"
_ADJUST = "finance.adjust"


def _entry_to_response(entry) -> JournalEntryResponse:
    return JournalEntryResponse(
        id=entry.id,
        code=entry.code,
        entry_date=entry.entry_date,
        narration=entry.narration,
        source=entry.source,
        status=entry.status,
        currency=entry.currency,
        reference_type=entry.reference_type,
        reference_id=entry.reference_id,
        reverses_entry_id=entry.reverses_entry_id,
        posted_at=entry.posted_at,
        posted_by=entry.posted_by,
        extra=entry.extra,
        lines=[JournalLineResponse.model_validate(l) for l in entry.lines],
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


# ---------------- Consolidated dashboard ----------------


@router.get(
    "/dashboard",
    summary="Finance consolidated dashboard — KPI rollup across modules",
    dependencies=[Depends(requires_permission(_READ))],
)
async def finance_dashboard(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict:
    """Aggregates finance-relevant numbers from across modules so the
    finance manager has one screen instead of clicking through 8.

    Returns:
      - escrow_active_count, escrow_active_minor (M4 disputes locking seller payout)
      - escrow_released_to_buyer_minor_30d (refunds via dispute resolution)
      - ad_revenue_minor_30d, ad_clicks_30d (M1 sponsored products)
      - cod_pending_minor (rider COD not yet deposited)
      - refunds_pending_count, refunds_pending_minor (finance refunds queue)
      - supplier_payouts_pending_count (M33 approval workflow)
      - wallet_balance_total_minor (sum of customer wallet balances)
    All counts soft-fail to 0 when source table is empty / unavailable.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select, text

    since = datetime.now(timezone.utc) - timedelta(days=window_days)

    async def _scalar(session, sql: str, params: dict | None = None) -> int:
        try:
            r = (
                await session.execute(text(sql), params or {})
            ).scalar_one_or_none()
            return int(r or 0)
        except Exception:  # noqa: BLE001
            return 0

    async with uow.transactional() as session:
        # M4 escrow holds
        escrow_active_count = await _scalar(
            session,
            "SELECT COUNT(*) FROM hypershop_escrow_holds WHERE status = 'active'",
        )
        escrow_active_minor = await _scalar(
            session,
            "SELECT COALESCE(SUM(held_amount_minor), 0) "
            "FROM hypershop_escrow_holds WHERE status = 'active'",
        )
        escrow_released_buyer_30d = await _scalar(
            session,
            "SELECT COALESCE(SUM(released_to_buyer_minor), 0) "
            "FROM hypershop_escrow_holds "
            "WHERE released_at >= :since",
            {"since": since},
        )

        # M1 ad revenue
        ad_revenue_30d = await _scalar(
            session,
            "SELECT COALESCE(SUM(charged_amount_minor), 0) "
            "FROM hypershop_ad_clicks "
            "WHERE is_invalid = FALSE AND created_at >= :since",
            {"since": since},
        )
        ad_clicks_30d = await _scalar(
            session,
            "SELECT COUNT(*) FROM hypershop_ad_clicks "
            "WHERE is_invalid = FALSE AND created_at >= :since",
            {"since": since},
        )

        # Customer wallet aggregate
        wallet_total = await _scalar(
            session,
            "SELECT COALESCE(SUM(balance_minor), 0) "
            "FROM hypershop_wallets WHERE status = 'ACTIVE'",
        )

        # Refunds queue (finance module)
        refunds_pending_count = await _scalar(
            session,
            "SELECT COUNT(*) FROM finance_refunds "
            "WHERE status IN ('pending','queued','approved')",
        )
        refunds_pending_minor = await _scalar(
            session,
            "SELECT COALESCE(SUM(amount_minor), 0) FROM finance_refunds "
            "WHERE status IN ('pending','queued','approved')",
        )

        # COD pending deposit (rider cash on hand)
        cod_pending_minor = await _scalar(
            session,
            "SELECT COALESCE(SUM(amount_minor), 0) FROM finance_cod_deposits "
            "WHERE status = 'pending'",
        )

        # Supplier payouts pending approval
        supplier_payouts_pending = await _scalar(
            session,
            "SELECT COUNT(*) FROM hypershop_supplier_bills "
            "WHERE status IN ('awaiting_approval','approved_pending_payment')",
        )

    return {
        "window_days": window_days,
        "escrow": {
            "active_count": escrow_active_count,
            "active_minor": escrow_active_minor,
            "released_to_buyer_30d_minor": escrow_released_buyer_30d,
        },
        "ad_revenue": {
            "total_minor": ad_revenue_30d,
            "click_count": ad_clicks_30d,
            "avg_cpc_minor": (
                round(ad_revenue_30d / ad_clicks_30d)
                if ad_clicks_30d > 0 else 0
            ),
        },
        "wallets": {"customer_balance_total_minor": wallet_total},
        "refunds_pending": {
            "count": refunds_pending_count,
            "amount_minor": refunds_pending_minor,
        },
        "cod_pending": {"amount_minor": cod_pending_minor},
        "supplier_payouts_pending": {"count": supplier_payouts_pending},
    }


# ---------------- Accounts ----------------


@router.get(
    "/accounts",
    response_model=list[AccountResponse],
    summary="List the chart of accounts",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_accounts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    type: str | None = Query(default=None),
    active_only: bool = Query(default=False),
) -> list[AccountResponse]:
    async with uow.transactional() as session:
        repo = AccountRepository(session)
        rows = await repo.list_all(type=type, active_only=active_only)
        return [AccountResponse.model_validate(a) for a in rows]


@router.post(
    "/accounts/seed",
    response_model=list[AccountResponse],
    summary="Idempotently seed the canonical chart of accounts",
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def seed_accounts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[AccountResponse]:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        await svc.ensure_chart_of_accounts()
        rows = await svc.accounts.list_all()
        return [AccountResponse.model_validate(a) for a in rows]


@router.patch(
    "/accounts/{account_id}",
    response_model=AccountResponse,
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def update_account(
    account_id: UUID,
    payload: AccountUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AccountResponse:
    fields = payload.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        svc = FinanceService(session)
        acc = await svc.update_account(
            principal=principal, account_id=account_id, fields=fields,
        )
        return AccountResponse.model_validate(acc)


# ---------------- Journal entries ----------------


@router.post(
    "/journal-entries",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post a manual journal entry (must be balanced)",
    dependencies=[Depends(requires_permission(_POST))],
)
async def create_journal_entry(
    payload: JournalEntryCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> JournalEntryResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        lines = [
            LineSpec(
                l.account_code,
                debit=l.debit,
                credit=l.credit,
                memo=l.memo,
            )
            for l in payload.lines
        ]
        entry = await svc.post_entry(
            actor=principal,
            entry_date=payload.entry_date,
            narration=payload.narration,
            lines=lines,
            source="manual",
            currency=payload.currency,
            reference_type=payload.reference_type,
            reference_id=payload.reference_id,
        )
        return _entry_to_response(entry)


@router.get(
    "/journal-entries",
    response_model=Page[JournalEntryResponse],
    summary="List posted (or all) journal entries in a date range",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_journal_entries(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    starts_on: date = Query(...),
    ends_on: date = Query(...),
    status_filter: str | None = Query(default="posted", alias="status"),
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=50, ge=1, le=100),
) -> Page[JournalEntryResponse]:
    if starts_on > ends_on:
        raise ValidationError("starts_on must be ≤ ends_on")
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = JournalRepository(session)
        rows, total = await repo.list_for_period(
            starts_on=starts_on,
            ends_on=ends_on,
            status=status_filter,
            offset=params.offset,
            limit=params.limit,
        )
    return Page.build(
        items=[_entry_to_response(e) for e in rows],
        total=total,
        params=params,
    )


@router.get(
    "/journal-entries/{entry_id}",
    response_model=JournalEntryResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_journal_entry(
    entry_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> JournalEntryResponse:
    async with uow.transactional() as session:
        repo = JournalRepository(session)
        entry = await repo.get(entry_id)
        if entry is None:
            from app.core.errors import NotFoundError
            raise NotFoundError("Journal entry not found.")
        return _entry_to_response(entry)


@router.post(
    "/journal-entries/{entry_id}/reverse",
    response_model=JournalEntryResponse,
    summary="Reverse a posted journal entry by inserting a mirror entry",
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def reverse_journal_entry(
    entry_id: UUID,
    payload: JournalEntryReverseRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> JournalEntryResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        reversal = await svc.reverse_entry(
            actor=principal,
            entry_id=entry_id,
            reason=payload.reason,
            reverse_date=payload.entry_date,
        )
        return _entry_to_response(reversal)


# ---------------- Periods + close ----------------


@router.get(
    "/periods",
    response_model=list[PeriodResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_periods(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[PeriodResponse]:
    async with uow.transactional() as session:
        repo = PeriodRepository(session)
        rows = await repo.list_all()
        return [PeriodResponse.model_validate(p) for p in rows]


@router.post(
    "/periods/close",
    response_model=PeriodResponse,
    summary="Lock an accounting period (rejects further posts to that month)",
    dependencies=[Depends(requires_permission(_CLOSE))],
)
async def close_period(
    payload: PeriodCloseRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PeriodResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        p = await svc.close_period(
            principal=principal,
            year=payload.year,
            month=payload.month,
            notes=payload.notes,
        )
        return PeriodResponse.model_validate(p)


@router.post(
    "/daily-close",
    response_model=DailyCloseResponse,
    summary="End-of-day close: snapshot totals and lock further posts ≤ that date",
    dependencies=[Depends(requires_permission(_CLOSE))],
)
async def daily_close(
    payload: DailyCloseRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DailyCloseResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        c = await svc.daily_close(
            principal=principal,
            closing_date=payload.closing_date,
            notes=payload.notes,
        )
        return DailyCloseResponse.model_validate(c)


@router.get(
    "/daily-close",
    response_model=list[DailyCloseResponse],
    summary="Recent daily closes",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_daily_closes(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = Query(default=30, ge=1, le=365),
) -> list[DailyCloseResponse]:
    async with uow.transactional() as session:
        repo = DailyCloseRepository(session)
        rows = await repo.list_recent(limit=limit)
        return [DailyCloseResponse.model_validate(r) for r in rows]


# ---------------- Reports ----------------


@router.get(
    "/reports/trial-balance",
    response_model=TrialBalanceResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def trial_balance(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    as_of: date = Query(...),
) -> TrialBalanceResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        return TrialBalanceResponse(**await svc.trial_balance(as_of=as_of))


@router.get(
    "/reports/profit-and-loss",
    response_model=PnLReport,
    dependencies=[Depends(requires_permission(_READ))],
)
async def profit_and_loss(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    starts_on: date = Query(...),
    ends_on: date = Query(...),
) -> PnLReport:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        return PnLReport(
            **await svc.profit_and_loss(starts_on=starts_on, ends_on=ends_on),
        )


@router.get(
    "/reports/balance-sheet",
    response_model=BalanceSheetReport,
    dependencies=[Depends(requires_permission(_READ))],
)
async def balance_sheet(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    as_of: date = Query(...),
) -> BalanceSheetReport:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        return BalanceSheetReport(**await svc.balance_sheet(as_of=as_of))


@router.get(
    "/reports/vat-ledger",
    response_model=VatLedgerResponse,
    summary="VAT-Payable ledger lines for a date range",
    dependencies=[Depends(requires_permission(_READ))],
)
async def vat_ledger(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    starts_on: date = Query(...),
    ends_on: date = Query(...),
) -> VatLedgerResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        return VatLedgerResponse(
            **await svc.vat_ledger(starts_on=starts_on, ends_on=ends_on),
        )


# ---------------- Supplier bills + payments ----------------


@router.post(
    "/supplier-bills",
    response_model=SupplierBillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a supplier bill (Dr Inventory / Cr AP-Suppliers)",
    dependencies=[Depends(requires_permission(_POST))],
)
async def book_supplier_bill(
    payload: SupplierBillCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SupplierBillResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        bill = await svc.book_supplier_bill(
            principal=principal,
            supplier_id=payload.supplier_id,
            bill_number=payload.bill_number,
            bill_date=payload.bill_date,
            due_date=payload.due_date,
            currency=payload.currency,
            subtotal=payload.subtotal,
            tax_total=payload.tax_total,
            goods_receipt_id=payload.goods_receipt_id,
            notes=payload.notes,
        )
        return SupplierBillResponse.model_validate(bill)


@router.get(
    "/supplier-bills",
    response_model=Page[SupplierBillResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_supplier_bills(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    supplier_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=50, ge=1, le=100),
) -> Page[SupplierBillResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = SupplierBillRepository(session)
        rows, total = await repo.list_all(
            supplier_id=supplier_id,
            status=status_filter,
            offset=params.offset,
            limit=params.limit,
        )
    return Page.build(
        items=[SupplierBillResponse.model_validate(b) for b in rows],
        total=total,
        params=params,
    )


@router.post(
    "/supplier-bills/{bill_id}/payments",
    response_model=SupplierPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Pay a supplier bill (Dr AP-Suppliers / Cr Bank)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def pay_supplier_bill(
    bill_id: UUID,
    payload: SupplierPaymentCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SupplierPaymentResponse:
    if payload.bill_id != bill_id:
        raise ValidationError("bill_id in path must match payload.")
    async with uow.transactional() as session:
        svc = FinanceService(session)
        pay = await svc.pay_supplier_bill(
            principal=principal,
            bill_id=bill_id,
            paid_on=payload.paid_on,
            amount=payload.amount,
            method=payload.method,
            bank_reference=payload.bank_reference,
            notes=payload.notes,
        )
        return SupplierPaymentResponse.model_validate(pay)


@router.get(
    "/supplier-bills/{bill_id}/payments",
    response_model=list[SupplierPaymentResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_supplier_payments(
    bill_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[SupplierPaymentResponse]:
    async with uow.transactional() as session:
        repo = SupplierBillRepository(session)
        rows = await repo.list_payments(bill_id=bill_id)
        return [SupplierPaymentResponse.model_validate(p) for p in rows]


# ---------------- COD deposits ----------------


@router.post(
    "/cod-deposits",
    response_model=CodDepositResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a rider's bank deposit (clears Cash-on-Hand-Rider)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def record_cod_deposit(
    payload: CodDepositCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CodDepositResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        d = await svc.record_cod_deposit(
            principal=principal,
            rider_id=payload.rider_id,
            deposit_date=payload.deposit_date,
            expected_amount=payload.expected_amount,
            deposited_amount=payload.deposited_amount,
            bank_reference=payload.bank_reference,
            notes=payload.notes,
        )
        return CodDepositResponse.model_validate(d)


@router.get(
    "/cod-deposits/rider/{rider_id}",
    response_model=list[CodDepositResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_cod_deposits_for_rider(
    rider_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[CodDepositResponse]:
    async with uow.transactional() as session:
        repo = CodDepositRepository(session)
        rows = await repo.list_for_rider(rider_id=rider_id, limit=limit)
        return [CodDepositResponse.model_validate(d) for d in rows]


@router.get(
    "/riders/{rider_id}/cash-on-hand",
    response_model=RiderCashOnHandResponse,
    summary="Outstanding cash a rider still owes the bank",
    dependencies=[Depends(requires_permission(_READ))],
)
async def rider_cash_on_hand(
    rider_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> RiderCashOnHandResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        return RiderCashOnHandResponse(**await svc.rider_cash_on_hand(rider_id))


# ---------------- Refunds ----------------


@router.get(
    "/refunds",
    response_model=Page[RefundRecordResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_refunds(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=50, ge=1, le=100),
) -> Page[RefundRecordResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = RefundRepository(session)
        rows, total = await repo.list_all(
            status=status_filter,
            offset=params.offset,
            limit=params.limit,
        )
    return Page.build(
        items=[RefundRecordResponse.model_validate(r) for r in rows],
        total=total,
        params=params,
    )


@router.post(
    "/refunds/pay",
    response_model=RefundRecordResponse,
    summary="Pay a pending refund (Dr Refund-Payable / Cr Bank)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def pay_refund(
    payload: RefundPaymentRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RefundRecordResponse:
    async with uow.transactional() as session:
        svc = FinanceService(session)
        record = await svc.pay_refund(
            principal=principal,
            refund_id=payload.refund_id,
            paid_on=payload.paid_on,
            amount=payload.amount,
            method=payload.method,
            bank_reference=payload.bank_reference,
            notes=payload.notes,
        )
        return RefundRecordResponse.model_validate(record)
