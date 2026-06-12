"""Gift cards admin endpoints — issue, list, revoke.

  GET    /admin/gift-cards               — list (filter by status)
  POST   /admin/gift-cards               — issue (admin generates code + face value)
  POST   /admin/gift-cards/{id}/revoke   — mark revoked
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ConflictError, NotFoundError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.gift_cards.models import GiftCard

router = APIRouter(prefix="/admin/gift-cards", tags=["admin-gift-cards"])
_W = "gift_cards.admin"


class GiftCardOut(BaseModel):
    id: UUID
    code: str
    face_value_minor: int
    currency: str
    status: str
    redeemed_by_user_id: UUID | None
    redeemed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class GiftCardListOut(BaseModel):
    items: list[GiftCardOut]
    total: int


class GiftCardIssueIn(BaseModel):
    face_value_minor: int = Field(ge=1)
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    code: str | None = Field(
        default=None,
        description="Optional explicit code; otherwise a 12-char random one is generated.",
    )
    expires_at: datetime | None = None


def _gen_code() -> str:
    """12-char alphanumeric, uppercase, biased toward unambiguous chars."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


@router.get(
    "",
    response_model=GiftCardListOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def list_gift_cards(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
    status_filter: Annotated[
        Literal["active", "redeemed", "expired", "revoked"] | None,
        Query(alias="status"),
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> GiftCardListOut:
    async with uow.transactional() as session:
        q = select(GiftCard)
        cnt = select(func.count(GiftCard.id))
        if status_filter:
            q = q.where(GiftCard.status == status_filter)
            cnt = cnt.where(GiftCard.status == status_filter)
        total = (await session.execute(cnt)).scalar_one()
        rows = (
            await session.execute(
                q.order_by(GiftCard.created_at.desc()).offset(offset).limit(limit)
            )
        ).scalars().all()
    return GiftCardListOut(
        items=[GiftCardOut.model_validate(r) for r in rows],
        total=int(total),
    )


@router.post(
    "",
    response_model=GiftCardOut,
    status_code=201,
    dependencies=[Depends(requires_permission(_W))],
)
async def issue_gift_card(
    payload: GiftCardIssueIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
) -> GiftCardOut:
    code = (payload.code or _gen_code()).upper().strip()
    async with uow.transactional() as session:
        # Reject duplicate codes — surface a clear error rather than
        # the generic IntegrityError.
        dup = (
            await session.execute(select(GiftCard).where(GiftCard.code == code))
        ).scalar_one_or_none()
        if dup is not None:
            raise ConflictError(f"Gift card code {code} already exists.")
        row = GiftCard(
            code=code,
            face_value_minor=payload.face_value_minor,
            currency=payload.currency.upper(),
            status="active",
            expires_at=payload.expires_at,
        )
        session.add(row)
        await session.flush()
    return GiftCardOut.model_validate(row)


@router.post(
    "/{gift_card_id}/revoke",
    response_model=GiftCardOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def revoke_gift_card(
    gift_card_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
) -> GiftCardOut:
    async with uow.transactional() as session:
        row = (
            await session.execute(select(GiftCard).where(GiftCard.id == gift_card_id))
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Gift card not found.")
        if row.status == "redeemed":
            raise ConflictError("Cannot revoke an already-redeemed card.")
        row.status = "revoked"
        await session.flush()
    return GiftCardOut.model_validate(row)
