"""Rider-app facing wallet endpoints.

The rider authenticates as a User; we resolve their Rider record via
``Rider.linked_user_id`` and then operate on that rider's wallet. This
keeps the URL short (``/rider/wallet`` not ``/rider/{rider_id}/wallet``)
and prevents one rider from peeking at another's wallet by changing
the URL.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ForbiddenError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.deliveries.models import Rider
from app.modules.rider_wallet.repository import (
    RiderSettlementRepository,
    RiderWalletDailySummaryRepository,
    RiderWalletLedgerRepository,
    RiderWalletRepository,
)
from app.modules.rider_wallet.cod_recharge import RiderCodRechargeService
from app.modules.rider_wallet.schemas import (
    ClearanceStatusResponse,
    CodRechargeInitiateRequest,
    CodRechargeInitiateResponse,
    CodRechargeVerifyRequest,
    CodRechargeVerifyResponse,
    DailySummaryListResponse,
    DailySummaryResponse,
    LedgerEntryResponse,
    LedgerListResponse,
    SettlementResponse,
    SettlementSubmitRequest,
    ShiftCloseRequest,
    WalletResponse,
)
from app.modules.rider_wallet.service import (
    AssignmentEligibilityService,
    RiderSettlementService,
    RiderWalletService,
    ShiftClosureService,
)

router = APIRouter(prefix="/rider", tags=["rider-wallet"])


async def _resolve_rider(
    *,
    session,
    principal: Principal,
) -> Rider:
    """Find the Rider row whose linked_user_id == principal.user_id."""
    rider = (
        await session.execute(
            select(Rider).where(Rider.linked_user_id == principal.user_id),
        )
    ).scalar_one_or_none()
    if rider is None or not rider.is_active:
        raise ForbiddenError(
            "Authenticated user is not bound to an active Rider record.",
        )
    return rider


# ----------------------------------------------------------------------
# Wallet read
# ----------------------------------------------------------------------
@router.get(
    "/wallet",
    response_model=WalletResponse,
    summary="Current rider's wallet snapshot",
)
async def wallet_overview(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WalletResponse:
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        svc = RiderWalletService(session)
        wallet = await svc.get_or_create(
            rider_id=rider.id, principal=principal,
        )
    return _wallet_to_response(wallet)


@router.get(
    "/wallet/clearance-status",
    response_model=ClearanceStatusResponse,
    summary="Quick check for the rider app to render lock state",
)
async def clearance_status(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ClearanceStatusResponse:
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        wallets = RiderWalletRepository(session)
        wallet = await wallets.get_by_rider(rider.id)
        if wallet is None:
            return ClearanceStatusResponse(
                rider_id=rider.id,
                assignment_locked=False,
                wallet_status="clear",
                payable_amount=0,
                pending_settlement=0,
                carry_forward_approved=False,
                carry_forward_active=False,
                reason=None,
            )
        eligibility = AssignmentEligibilityService(session)
        ok, reason = await eligibility.check(rider_id=rider.id)
        now = utc_now()
        cf_active = (
            wallet.carry_forward_approved
            and wallet.carry_forward_expires_at is not None
            and wallet.carry_forward_expires_at > now
        )
        return ClearanceStatusResponse(
            rider_id=rider.id,
            assignment_locked=wallet.assignment_locked,
            wallet_status=wallet.wallet_status,
            payable_amount=wallet.wallet_payable_to_company,
            pending_settlement=wallet.wallet_pending_settlement,
            carry_forward_approved=wallet.carry_forward_approved,
            carry_forward_active=cf_active,
            reason=None if ok else reason,
        )


@router.get(
    "/wallet/ledger",
    response_model=LedgerListResponse,
    summary="Recent ledger lines for the current rider",
)
async def wallet_ledger(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> LedgerListResponse:
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        repo = RiderWalletLedgerRepository(session)
        rows = await repo.list_for_rider(rider.id, limit=limit)
        return LedgerListResponse(items=[
            _ledger_to_response(r) for r in rows
        ])


@router.get(
    "/wallet/daily-summary",
    response_model=DailySummaryListResponse,
    summary="Recent per-day closing summaries for the current rider",
)
async def wallet_daily_summary(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=90)] = 30,
) -> DailySummaryListResponse:
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        repo = RiderWalletDailySummaryRepository(session)
        rows = await repo.list_recent(rider.id, limit=limit)
        return DailySummaryListResponse(items=[
            _summary_to_response(r) for r in rows
        ])


@router.get(
    "/wallet/settlements",
    response_model=list[SettlementResponse],
    summary="Recent MFS settlement submissions by the current rider",
)
async def wallet_settlements(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SettlementResponse]:
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        repo = RiderSettlementRepository(session)
        rows = await repo.list_for_rider(rider.id, limit=limit)
        return [_settlement_to_response(r) for r in rows]


# ----------------------------------------------------------------------
# Settlement submission
# ----------------------------------------------------------------------
@router.post(
    "/wallet/settlements",
    response_model=SettlementResponse,
    status_code=201,
    summary="Submit a new MFS settlement claim (awaits finance verification)",
)
async def submit_settlement(
    body: SettlementSubmitRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SettlementResponse:
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        # Use the configured company receiver from settings; falls back
        # to a generic placeholder (ops should set REPORT_*_ACCOUNT
        # in production).
        from app.core.config import get_settings
        settings = get_settings()
        company_account = getattr(
            settings, "rider_wallet_company_receiver_account",
            "017XXXXXXXX",
        )
        svc = RiderSettlementService(session)
        row = await svc.submit(
            rider_id=rider.id,
            shift_id=body.shift_id,
            mfs_provider=body.mfs_provider,
            sender_mfs_number=body.sender_mfs_number,
            company_receiver_account=company_account,
            submitted_amount=body.submitted_amount,
            transaction_id=body.transaction_id,
            transaction_time=body.transaction_time,
            proof_image_url=body.proof_image_url,
            principal=principal,
        )
    return _settlement_to_response(row)


# ----------------------------------------------------------------------
# Shift close request
# ----------------------------------------------------------------------
@router.post(
    "/wallet/shift-close",
    response_model=DailySummaryResponse,
    summary="Request shift close + write the per-day summary (idempotent)",
)
async def request_shift_close(
    body: ShiftCloseRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DailySummaryResponse:
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        svc = ShiftClosureService(session)
        summary = await svc.request_close(
            rider_id=rider.id,
            shift_id=body.shift_id,
            summary_date=body.summary_date,
            principal=principal,
        )
    return _summary_to_response(summary)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _wallet_to_response(w) -> WalletResponse:
    return WalletResponse(
        id=w.id,
        rider_id=w.rider_id,
        cash_in_hand=w.cash_in_hand,
        wallet_payable_to_company=w.wallet_payable_to_company,
        wallet_pending_settlement=w.wallet_pending_settlement,
        wallet_credit_adjustment=w.wallet_credit_adjustment,
        wallet_status=w.wallet_status,
        last_settlement_at=w.last_settlement_at,
        is_frozen=w.is_frozen,
        assignment_locked=w.assignment_locked,
        assignment_locked_reason=w.assignment_locked_reason,
        overdue_since=w.overdue_since,
        carry_forward_approved=w.carry_forward_approved,
        carry_forward_amount=w.carry_forward_amount,
        carry_forward_expires_at=w.carry_forward_expires_at,
    )


def _ledger_to_response(r) -> LedgerEntryResponse:
    return LedgerEntryResponse(
        id=r.id,
        rider_id=r.rider_id,
        shift_id=r.shift_id,
        delivery_assignment_id=r.delivery_assignment_id,
        settlement_id=r.settlement_id,
        entry_type=r.entry_type,
        direction=r.direction,
        amount=r.amount,
        balance_after=r.balance_after,
        note=r.note,
        created_at=r.created_at,
    )


def _settlement_to_response(s) -> SettlementResponse:
    return SettlementResponse(
        id=s.id,
        rider_id=s.rider_id,
        shift_id=s.shift_id,
        settlement_date=s.settlement_date,
        mfs_provider=s.mfs_provider,
        sender_mfs_number=s.sender_mfs_number,
        company_receiver_account=s.company_receiver_account,
        submitted_amount=s.submitted_amount,
        verified_amount=s.verified_amount,
        transaction_id=s.transaction_id,
        transaction_time=s.transaction_time,
        proof_image_url=s.proof_image_url,
        status=s.status,
        review_note=s.review_note,
        reviewed_at=s.reviewed_at,
        created_at=s.created_at,
    )


def _summary_to_response(s) -> DailySummaryResponse:
    return DailySummaryResponse(
        id=s.id,
        rider_id=s.rider_id,
        shift_id=s.shift_id,
        summary_date=s.summary_date,
        total_cod_collected=s.total_cod_collected,
        total_submitted=s.total_submitted,
        total_verified=s.total_verified,
        total_pending=s.total_pending,
        closing_payable=s.closing_payable,
        clearance_status=s.clearance_status,
        is_cleared_for_next_shift=s.is_cleared_for_next_shift,
        blocked_amount=s.blocked_amount,
    )


# ----------------------------------------------------------------------
# COD recharge (Track A — Bkash Tokenized Checkout)
# ----------------------------------------------------------------------
@router.post(
    "/wallet/cod/recharge/initiate",
    response_model=CodRechargeInitiateResponse,
    status_code=201,
    summary="Open a Bkash session to push collected COD to the company merchant",
)
async def cod_recharge_initiate(
    body: CodRechargeInitiateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CodRechargeInitiateResponse:
    from app.core.config import get_settings
    settings = get_settings()
    api_base_url = (
        getattr(settings, "public_api_base_url", None)
        or getattr(settings, "api_base_url", None)
        or "https://api.hypershop.com.bd"
    )
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        svc = RiderCodRechargeService(session)
        row = await svc.initiate(
            rider_id=rider.id,
            amount=body.amount,
            mfs_number=body.mfs_number,
            idempotency_key=body.idempotency_key,
            api_base_url=api_base_url,
            principal=principal,
        )
    from app.modules.rider_wallet.cod_recharge import _mask_phone
    return CodRechargeInitiateResponse(
        recharge_session_id=row.id,
        bkash_url=row.bkash_url or "",
        provider_payment_id=row.provider_payment_id or "",
        expires_at=row.expires_at or utc_now(),
        masked_payer_number=_mask_phone(row.payer_mfs_number),
    )


@router.post(
    "/wallet/cod/recharge/verify",
    response_model=CodRechargeVerifyResponse,
    summary="Capture the Bkash payment and credit the rider's settlement",
)
async def cod_recharge_verify(
    body: CodRechargeVerifyRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CodRechargeVerifyResponse:
    async with uow.transactional() as session:
        rider = await _resolve_rider(session=session, principal=principal)
        svc = RiderCodRechargeService(session)
        row = await svc.verify(
            recharge_session_id=body.recharge_session_id,
            provider_payment_id=body.provider_payment_id,
            principal=principal,
            rider_id=rider.id,
        )
        wallets = RiderWalletRepository(session)
        wallet = await wallets.get_by_rider_id(rider.id)
    return CodRechargeVerifyResponse(
        recharge_session_id=row.id,
        status=row.status,
        captured_amount_bdt=row.captured_amount,
        provider_trx_id=row.provider_trx_id,
        failure_reason=row.failure_reason,
        wallet_payable_to_company_after=(
            wallet.wallet_payable_to_company if wallet else 0
        ),
    )


@router.get(
    "/wallet/cod/recharge/callback",
    summary="Bkash redirect target after rider finishes hosted payment page",
)
async def cod_recharge_callback(
    session: UUID = Query(..., description="Recharge session id"),
    paymentID: str | None = Query(default=None),
    status: str | None = Query(default=None),
    uow: Annotated[UnitOfWork, Depends(get_uow)] = None,
):
    """Tiny landing page that the rider app's WebView intercepts.

    The rider app reads ``paymentID`` and ``status`` from the URL itself
    via WebViewClient.shouldOverrideUrlLoading, so this body does NOT
    need to render anything user-facing. We still flush an
    `in_progress` marker on the session row so admins can see the
    callback fired even before the app calls /verify.
    """
    if uow is not None:
        async with uow.transactional() as db:
            svc = RiderCodRechargeService(db)
            await svc.mark_in_progress(recharge_session_id=session)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Hypershop — Bkash callback</title>"
        "<body style='font-family:sans-serif;text-align:center;padding:40px'>"
        "<h2>Payment received</h2>"
        "<p>You may return to the Hypershop Rider app.</p></body>",
        status_code=200,
    )
