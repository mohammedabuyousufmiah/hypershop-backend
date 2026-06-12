"""Admin/finance endpoints for rider wallet + settlement.

All endpoints require ``rider.dispatch`` permission (the same one used
by Module 31's admin dispatch endpoints — finance/ops both have it).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.rider_wallet.errors import (
    SettlementNotFoundError,
    WalletNotFoundError,
)
from app.modules.rider_wallet.repository import (
    RiderCashLimitRepository,
    RiderSettlementRepository,
    RiderWalletDailySummaryRepository,
    RiderWalletLedgerRepository,
    RiderWalletRepository,
)
from app.modules.rider_wallet.schemas import (
    CarryForwardApproveRequest,
    CarryForwardRejectRequest,
    CashLimitResponse,
    CashLimitUpdateRequest,
    DailySummaryListResponse,
    FreezeRequest,
    LedgerListResponse,
    SettlementListResponse,
    SettlementRejectRequest,
    SettlementResponse,
    SettlementVerifyRequest,
    UnlockRequest,
    WalletListResponse,
    WalletResponse,
)
from app.modules.rider_wallet.service import (
    RiderSettlementService,
    RiderWalletService,
)

router = APIRouter(
    prefix="/admin/rider-wallets",
    tags=["admin-rider-wallets"],
)

_DISPATCH = "rider.dispatch"

# Cash-settlement endpoints — the verify / reject pair posts the
# COD handover from rider → finance desk. Tightened 2026-05-16 from
# the broad `rider.dispatch` gate (held by dispatcher) to the new
# `riders.cod_settle` perm scoped to admin / finance_manager /
# rider_manager. Separation of duties: dispatch ops still moves
# riders + run sheets; cash approval flows through finance.
_COD_SETTLE = "riders.cod_settle"


# ----------------------------------------------------------------------
# Wallet read
# ----------------------------------------------------------------------
@router.get(
    "",
    response_model=WalletListResponse,
    summary="List wallets (highest-payable first)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def list_wallets(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> WalletListResponse:
    async with uow.transactional() as session:
        repo = RiderWalletRepository(session)
        rows = await repo.list_all(limit=limit)
    from app.modules.rider_wallet.api.rider import _wallet_to_response
    return WalletListResponse(items=[_wallet_to_response(r) for r in rows])


@router.get(
    "/blocked",
    response_model=WalletListResponse,
    summary="List wallets with assignment_locked=true",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def list_blocked(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> WalletListResponse:
    async with uow.transactional() as session:
        repo = RiderWalletRepository(session)
        rows = await repo.list_blocked(limit=limit)
    from app.modules.rider_wallet.api.rider import _wallet_to_response
    return WalletListResponse(items=[_wallet_to_response(r) for r in rows])


@router.get(
    "/{rider_id}",
    response_model=WalletResponse,
    summary="Wallet detail for a specific rider",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def get_wallet(
    rider_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> WalletResponse:
    async with uow.transactional() as session:
        repo = RiderWalletRepository(session)
        wallet = await repo.get_by_rider(rider_id)
        if wallet is None:
            raise WalletNotFoundError("Wallet not found.")
    from app.modules.rider_wallet.api.rider import _wallet_to_response
    return _wallet_to_response(wallet)


@router.get(
    "/{rider_id}/ledger",
    response_model=LedgerListResponse,
    summary="Recent ledger lines for a rider",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def get_rider_ledger(
    rider_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> LedgerListResponse:
    async with uow.transactional() as session:
        repo = RiderWalletLedgerRepository(session)
        rows = await repo.list_for_rider(rider_id, limit=limit)
    from app.modules.rider_wallet.api.rider import _ledger_to_response
    return LedgerListResponse(items=[_ledger_to_response(r) for r in rows])


@router.get(
    "/{rider_id}/daily-summaries",
    response_model=DailySummaryListResponse,
    summary="Recent per-day summaries for a rider",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def get_rider_summaries(
    rider_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=90)] = 30,
) -> DailySummaryListResponse:
    async with uow.transactional() as session:
        repo = RiderWalletDailySummaryRepository(session)
        rows = await repo.list_recent(rider_id, limit=limit)
    from app.modules.rider_wallet.api.rider import _summary_to_response
    return DailySummaryListResponse(
        items=[_summary_to_response(r) for r in rows],
    )


# ----------------------------------------------------------------------
# Settlement queue
# ----------------------------------------------------------------------
@router.get(
    "/settlements",
    response_model=SettlementListResponse,
    summary="Pending settlement review queue (submitted + under_review)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def list_pending_settlements(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SettlementListResponse:
    async with uow.transactional() as session:
        repo = RiderSettlementRepository(session)
        rows = await repo.list_pending(limit=limit)
    from app.modules.rider_wallet.api.rider import _settlement_to_response
    return SettlementListResponse(
        items=[_settlement_to_response(r) for r in rows],
    )


@router.post(
    "/settlements/{settlement_id}/verify",
    response_model=SettlementResponse,
    summary="Approve a settlement (full or partial via verified_amount)",
    dependencies=[Depends(requires_permission(_COD_SETTLE))],
)
async def verify_settlement(
    settlement_id: UUID,
    body: SettlementVerifyRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SettlementResponse:
    async with uow.transactional() as session:
        svc = RiderSettlementService(session)
        row = await svc.verify(
            settlement_id=settlement_id,
            verified_amount=body.verified_amount,
            principal=principal,
            review_note=body.review_note,
        )
    from app.modules.rider_wallet.api.rider import _settlement_to_response
    return _settlement_to_response(row)


@router.post(
    "/settlements/{settlement_id}/reject",
    response_model=SettlementResponse,
    summary="Reject a settlement (refunds wallet.pending → re-locks if unpaid)",
    dependencies=[Depends(requires_permission(_COD_SETTLE))],
)
async def reject_settlement(
    settlement_id: UUID,
    body: SettlementRejectRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SettlementResponse:
    async with uow.transactional() as session:
        svc = RiderSettlementService(session)
        row = await svc.reject(
            settlement_id=settlement_id,
            principal=principal,
            review_note=body.review_note,
        )
    from app.modules.rider_wallet.api.rider import _settlement_to_response
    return _settlement_to_response(row)


# ----------------------------------------------------------------------
# Lock / freeze / carry-forward
# ----------------------------------------------------------------------
@router.post(
    "/{rider_id}/freeze",
    response_model=WalletResponse,
    summary="Admin freeze — wallet rejects new settlements + assignment locked",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def freeze_wallet(
    rider_id: UUID,
    body: FreezeRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WalletResponse:
    async with uow.transactional() as session:
        svc = RiderWalletService(session)
        wallet = await svc.freeze(
            rider_id=rider_id, reason=body.reason, principal=principal,
        )
    from app.modules.rider_wallet.api.rider import _wallet_to_response
    return _wallet_to_response(wallet)


@router.post(
    "/{rider_id}/unfreeze",
    response_model=WalletResponse,
    summary="Lift a freeze (lock remains if there's still unpaid balance)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def unfreeze_wallet(
    rider_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WalletResponse:
    async with uow.transactional() as session:
        svc = RiderWalletService(session)
        wallet = await svc.unfreeze(rider_id=rider_id, principal=principal)
    from app.modules.rider_wallet.api.rider import _wallet_to_response
    return _wallet_to_response(wallet)


@router.post(
    "/{rider_id}/unlock",
    response_model=WalletResponse,
    summary="Manually clear assignment_locked (requires no freeze)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def unlock_wallet(
    rider_id: UUID,
    body: UnlockRequest,  # noqa: ARG001 — note kept in audit only
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WalletResponse:
    async with uow.transactional() as session:
        svc = RiderWalletService(session)
        wallet = await svc.unlock(rider_id=rider_id, principal=principal)
    from app.modules.rider_wallet.api.rider import _wallet_to_response
    return _wallet_to_response(wallet)


@router.post(
    "/{rider_id}/approve-carry-forward",
    response_model=WalletResponse,
    summary="Approve carry-forward — lifts the lock for the approved amount",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def approve_carry_forward(
    rider_id: UUID,
    body: CarryForwardApproveRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WalletResponse:
    async with uow.transactional() as session:
        svc = RiderWalletService(session)
        wallet = await svc.approve_carry_forward(
            rider_id=rider_id,
            amount=body.amount,
            expires_at=body.expires_at,
            principal=principal,
        )
    from app.modules.rider_wallet.api.rider import _wallet_to_response
    return _wallet_to_response(wallet)


@router.post(
    "/{rider_id}/reject-carry-forward",
    response_model=WalletResponse,
    summary="Revoke carry-forward — re-applies the lock if balance remains",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def reject_carry_forward(
    rider_id: UUID,
    body: CarryForwardRejectRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WalletResponse:
    async with uow.transactional() as session:
        svc = RiderWalletService(session)
        wallet = await svc.reject_carry_forward(
            rider_id=rider_id,
            principal=principal,
            reason=body.reason,
        )
    from app.modules.rider_wallet.api.rider import _wallet_to_response
    return _wallet_to_response(wallet)


# ----------------------------------------------------------------------
# Cash limits
# ----------------------------------------------------------------------
@router.put(
    "/{rider_id}/cash-limits",
    response_model=CashLimitResponse,
    summary="Set/update per-rider cash + carry-forward limits",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def update_cash_limits(
    rider_id: UUID,
    body: CashLimitUpdateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CashLimitResponse:
    async with uow.transactional() as session:
        svc = RiderWalletService(session)
        row = await svc.set_cash_limits(
            rider_id=rider_id,
            max_cash_in_hand=body.max_cash_in_hand,
            max_unsettled_amount=body.max_unsettled_amount,
            allow_carry_forward=body.allow_carry_forward,
            carry_forward_limit=body.carry_forward_limit,
            principal=principal,
        )
    return CashLimitResponse(
        id=row.id,
        rider_id=row.rider_id,
        max_cash_in_hand=row.max_cash_in_hand,
        max_unsettled_amount=row.max_unsettled_amount,
        allow_carry_forward=row.allow_carry_forward,
        carry_forward_limit=row.carry_forward_limit,
    )


@router.get(
    "/{rider_id}/cash-limits",
    response_model=CashLimitResponse | None,
    summary="Get a rider's cash + carry-forward limits",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def get_cash_limits(
    rider_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> CashLimitResponse | None:
    async with uow.transactional() as session:
        repo = RiderCashLimitRepository(session)
        row = await repo.get_by_rider(rider_id)
        if row is None:
            return None
        return CashLimitResponse(
            id=row.id,
            rider_id=row.rider_id,
            max_cash_in_hand=row.max_cash_in_hand,
            max_unsettled_amount=row.max_unsettled_amount,
            allow_carry_forward=row.allow_carry_forward,
            carry_forward_limit=row.carry_forward_limit,
        )
