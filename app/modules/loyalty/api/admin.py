"""Loyalty admin endpoints — view balances, ledgers, manual adjustments.

  GET  /admin/loyalty/accounts                            — paginated list
  GET  /admin/loyalty/accounts/{user_id}                  — detail + balance
  GET  /admin/loyalty/accounts/{user_id}/transactions     — ledger
  POST /admin/loyalty/accounts/{user_id}/adjust           — manual +/- adjust
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.loyalty.models import LoyaltyAccount, LoyaltyTransaction
from app.modules.loyalty.service import LoyaltyService

router = APIRouter(prefix="/admin/loyalty", tags=["admin-loyalty"])
_W = "loyalty.admin"


class LoyaltyAccountOut(BaseModel):
    user_id: UUID
    balance_points: int
    lifetime_earned_points: int
    tier: str
    model_config = ConfigDict(from_attributes=True)


class LoyaltyAccountListOut(BaseModel):
    items: list[LoyaltyAccountOut]
    total: int


class LoyaltyTxnOut(BaseModel):
    id: UUID
    user_id: UUID
    txn_type: str
    points: int
    reason: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LoyaltyTxnListOut(BaseModel):
    items: list[LoyaltyTxnOut]
    total: int


class LoyaltyAdjustIn(BaseModel):
    delta_points: int = Field(description="Positive credit, negative debit. Cannot be 0.")
    reason: str = Field(min_length=1, max_length=255)


@router.get(
    "/accounts",
    response_model=LoyaltyAccountListOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def list_accounts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> LoyaltyAccountListOut:
    async with uow.transactional() as session:
        total = (
            await session.execute(select(func.count(LoyaltyAccount.user_id)))
        ).scalar_one()
        rows = (
            await session.execute(
                select(LoyaltyAccount)
                .order_by(LoyaltyAccount.balance_points.desc())
                .offset(offset).limit(limit)
            )
        ).scalars().all()
    return LoyaltyAccountListOut(
        items=[LoyaltyAccountOut.model_validate(r) for r in rows],
        total=int(total),
    )


@router.get(
    "/accounts/{user_id}",
    response_model=LoyaltyAccountOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def get_account(
    user_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
) -> LoyaltyAccountOut:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Loyalty account not found.")
    return LoyaltyAccountOut.model_validate(row)


@router.get(
    "/accounts/{user_id}/transactions",
    response_model=LoyaltyTxnListOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def list_transactions(
    user_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> LoyaltyTxnListOut:
    async with uow.transactional() as session:
        total = (
            await session.execute(
                select(func.count(LoyaltyTransaction.id))
                .where(LoyaltyTransaction.user_id == user_id)
            )
        ).scalar_one()
        rows = (
            await session.execute(
                select(LoyaltyTransaction)
                .where(LoyaltyTransaction.user_id == user_id)
                .order_by(LoyaltyTransaction.created_at.desc())
                .offset(offset).limit(limit)
            )
        ).scalars().all()
    return LoyaltyTxnListOut(
        items=[LoyaltyTxnOut.model_validate(r) for r in rows],
        total=int(total),
    )


@router.post(
    "/accounts/{user_id}/adjust",
    response_model=LoyaltyAccountOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def adjust_account(
    user_id: UUID,
    payload: LoyaltyAdjustIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> LoyaltyAccountOut:
    async with uow.transactional() as session:
        svc = LoyaltyService(session)
        acc = await svc.adjust(
            user_id=user_id,
            delta_points=payload.delta_points,
            reason=payload.reason,
            actor_user_id=principal.user_id,
        )
    return LoyaltyAccountOut.model_validate(acc)
