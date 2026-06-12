"""Customer-facing wallet endpoints.

  GET /wallets/me                  — full wallet (matches FE WalletWire)
  GET /wallets/me/availability     — light-weight check used by checkout
  GET /wallets/me/transactions     — paginated txn history (newest first)

Mounted at the /wallets root (FE api.wallet.* expects this prefix).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.wallet.schemas import (
    WalletAvailabilityOut,
    WalletOut,
    WalletTxnListOut,
    WalletTxnOut,
)
from app.modules.wallet.service import WalletService, minor_to_decimal_str

router = APIRouter(prefix="/wallets", tags=["wallet"])

# Customer-self gate (same pattern as wishlist + support tickets).
_SELF = "iam.user.read.self"


def _wallet_to_out(w) -> WalletOut:
    return WalletOut(
        id=w.id,
        customer_id=w.customer_user_id,
        currency=w.currency,
        balance=minor_to_decimal_str(int(w.balance_minor)),
        status=w.status,  # type: ignore[arg-type]
        last_activity_at=w.last_activity_at,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


def _txn_to_out(t) -> WalletTxnOut:
    return WalletTxnOut(
        id=t.id,
        wallet_id=t.wallet_id,
        kind=t.kind,  # type: ignore[arg-type]
        amount=minor_to_decimal_str(int(t.amount_minor)),
        balance_after=minor_to_decimal_str(int(t.balance_after_minor)),
        source_type=t.source_type,
        source_id=t.source_id,
        memo=t.memo,
        created_at=t.created_at,
    )


@router.get(
    "/me",
    response_model=WalletOut,
    dependencies=[Depends(requires_permission(_SELF))],
    summary="Read the caller's e-commerce wallet",
)
async def get_my_wallet(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WalletOut:
    async with uow.transactional() as session:
        svc = WalletService(session)
        wallet = await svc.get_balance(user_id=principal.user_id)
    return _wallet_to_out(wallet)


@router.get(
    "/me/availability",
    response_model=WalletAvailabilityOut,
    dependencies=[Depends(requires_permission(_SELF))],
    summary="Wallet availability — used by checkout to decide how much to apply",
)
async def get_my_wallet_availability(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WalletAvailabilityOut:
    async with uow.transactional() as session:
        svc = WalletService(session)
        wallet = await svc.get_balance(user_id=principal.user_id)
    return WalletAvailabilityOut(
        available_balance=minor_to_decimal_str(int(wallet.balance_minor)),
        currency=wallet.currency,
        status=wallet.status,  # type: ignore[arg-type]
    )


@router.get(
    "/me/transactions",
    response_model=WalletTxnListOut,
    dependencies=[Depends(requires_permission(_SELF))],
    summary="Paginated wallet transaction history (newest first)",
)
async def list_my_wallet_transactions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> WalletTxnListOut:
    async with uow.transactional() as session:
        svc = WalletService(session)
        items, total = await svc.list_transactions(
            user_id=principal.user_id, offset=offset, limit=limit,
        )
    return WalletTxnListOut(
        items=[_txn_to_out(t) for t in items],
        total=total,
    )
