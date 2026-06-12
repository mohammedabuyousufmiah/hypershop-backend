"""Referrals admin gap endpoints — Phase-4 wire shape for AdminGrowthClient.

These serve the FE paths the growth panel calls:

  GET /referrals/admin/referrals          -> ReferralPage  {items, next_cursor}
  GET /referrals/admin/referrals/{id}     -> ReferralOut   (with rewards fanout)

The Phase-4 referral schema the FE expects (referrer_customer_id / invitee_*
/ 8-state status / reward fanout) is NOT backed by a table in this build — the
only referral tables are ``referral_codes`` + ``referral_attributions`` which
use a different (older) shape. So these endpoints return the EXACT wire shape
the FE reads but empty, so the panel connects + renders an empty state instead
of 404-ing. If/when a Phase-4 ``referrals`` table lands, swap the empty
constants for a real query — the response models already match.

Every query is wrapped defensively: a missing table/column yields an empty
result, never a 500.

Self-contained: registered centrally in main.py (do not import elsewhere).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/referrals/admin", tags=["admin-referrals"])

# Admins hold this; it is the same perm the existing referrals admin router and
# the admin-module registry use.
_PERM = "referrals.admin"


# ---------------------------------------------------------------------------
# Wire models — match @ecom/types ReferralSummaryWire / ReferralRewardWire /
# ReferralDetailWire / ReferralPageWire exactly.
# ---------------------------------------------------------------------------
class ReferralRewardOut(BaseModel):
    id: UUID
    referral_id: UUID
    beneficiary: str  # REFERRER | INVITEE
    kind: str  # WALLET_CREDIT | LOYALTY_POINTS | COUPON
    beneficiary_customer_id: UUID
    amount: float | None = None
    currency: str | None = None
    points: int | None = None
    coupon_id: UUID | None = None
    status: str  # PENDING | GRANTED | REVERSED
    ledger_reference_type: str | None = None
    ledger_reference_id: UUID | None = None
    granted_at: datetime | None = None
    reversed_at: datetime | None = None
    reversal_reason: str | None = None
    actor_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ReferralSummaryOut(BaseModel):
    id: UUID
    referrer_customer_id: UUID
    code: str
    invitee_email: str | None = None
    invitee_phone: str | None = None
    invitee_customer_id: UUID | None = None
    status: str
    qualifying_order_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ReferralPage(BaseModel):
    items: list[ReferralSummaryOut] = []
    next_cursor: str | None = None


class ReferralOut(BaseModel):
    id: UUID
    referrer_customer_id: UUID
    code: str
    invitee_email: str | None = None
    invitee_phone: str | None = None
    invitee_customer_id: UUID | None = None
    status: str
    qualifying_order_id: UUID | None = None
    invited_at: datetime | None = None
    signed_up_at: datetime | None = None
    qualified_at: datetime | None = None
    rewarded_at: datetime | None = None
    rejected_at: datetime | None = None
    reversed_at: datetime | None = None
    rejection_reason: str | None = None
    actor_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    rewards: list[ReferralRewardOut] = []
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Defensive probe — does a Phase-4 ``referrals`` table (with the columns the
# FE shape needs) exist? If not, we serve empty. This keeps the file boot-safe
# and the endpoint 500-proof regardless of which migration set is applied.
# ---------------------------------------------------------------------------
async def _referrals_table_ready(session: Any) -> bool:
    try:
        row = (
            await session.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'referrals' "
                    "AND column_name = 'referrer_customer_id' LIMIT 1"
                )
            )
        ).first()
        return row is not None
    except Exception:
        return False


def _rows_to_summaries(rows: list[Any]) -> list[ReferralSummaryOut]:
    out: list[ReferralSummaryOut] = []
    for r in rows:
        m = r._mapping if hasattr(r, "_mapping") else r
        try:
            out.append(
                ReferralSummaryOut(
                    id=m["id"],
                    referrer_customer_id=m["referrer_customer_id"],
                    code=m["code"],
                    invitee_email=m.get("invitee_email"),
                    invitee_phone=m.get("invitee_phone"),
                    invitee_customer_id=m.get("invitee_customer_id"),
                    status=m["status"],
                    qualifying_order_id=m.get("qualifying_order_id"),
                    created_at=m["created_at"],
                    updated_at=m.get("updated_at") or m["created_at"],
                )
            )
        except Exception:
            continue
    return out


@router.get(
    "/referrals",
    response_model=ReferralPage,
    dependencies=[Depends(requires_permission(_PERM))],
)
async def list_referrals(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    referrer_customer_id: Annotated[str | None, Query()] = None,
    invitee_customer_id: Annotated[str | None, Query()] = None,
    contact: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> ReferralPage:
    """List referral audit rows. Returns empty when the Phase-4 ``referrals``
    table is absent in this build (FE renders a calm empty state)."""
    try:
        async with uow.transactional() as session:
            if not await _referrals_table_ready(session):
                return ReferralPage(items=[], next_cursor=None)

            clauses: list[str] = []
            params: dict[str, Any] = {"lim": limit}
            if status_filter:
                clauses.append("status = :status")
                params["status"] = status_filter
            if referrer_customer_id:
                clauses.append("referrer_customer_id = :rid")
                params["rid"] = referrer_customer_id
            if invitee_customer_id:
                clauses.append("invitee_customer_id = :iid")
                params["iid"] = invitee_customer_id
            if contact:
                clauses.append(
                    "(invitee_email = :contact OR invitee_phone = :contact)"
                )
                params["contact"] = contact
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = (
                await session.execute(
                    text(
                        "SELECT id, referrer_customer_id, code, invitee_email, "
                        "invitee_phone, invitee_customer_id, status, "
                        "qualifying_order_id, created_at, updated_at "
                        "FROM referrals" + where
                        + " ORDER BY created_at DESC LIMIT :lim"
                    ),
                    params,
                )
            ).fetchall()
        return ReferralPage(items=_rows_to_summaries(list(rows)), next_cursor=None)
    except Exception:
        return ReferralPage(items=[], next_cursor=None)


@router.get(
    "/referrals/{referral_id}",
    response_model=ReferralOut | None,
    dependencies=[Depends(requires_permission(_PERM))],
)
async def get_referral(
    referral_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
) -> ReferralOut | None:
    """Single referral detail with reward fanout. Returns ``null`` when the
    Phase-4 table/row is absent — the FE treats a not-found detail gracefully."""
    try:
        async with uow.transactional() as session:
            if not await _referrals_table_ready(session):
                return None
            r = (
                await session.execute(
                    text(
                        "SELECT id, referrer_customer_id, code, invitee_email, "
                        "invitee_phone, invitee_customer_id, status, "
                        "qualifying_order_id, invited_at, signed_up_at, "
                        "qualified_at, rewarded_at, rejected_at, reversed_at, "
                        "rejection_reason, actor_id, created_at, updated_at "
                        "FROM referrals WHERE id = :id LIMIT 1"
                    ),
                    {"id": str(referral_id)},
                )
            ).first()
            if r is None:
                return None
            m = r._mapping
            rewards: list[ReferralRewardOut] = []
            try:
                rw_rows = (
                    await session.execute(
                        text(
                            "SELECT id, referral_id, beneficiary, kind, "
                            "beneficiary_customer_id, amount, currency, points, "
                            "coupon_id, status, ledger_reference_type, "
                            "ledger_reference_id, granted_at, reversed_at, "
                            "reversal_reason, actor_id, created_at, updated_at "
                            "FROM referral_rewards WHERE referral_id = :id "
                            "ORDER BY created_at ASC"
                        ),
                        {"id": str(referral_id)},
                    )
                ).fetchall()
                for rw in rw_rows:
                    try:
                        rewards.append(ReferralRewardOut(**dict(rw._mapping)))
                    except Exception:
                        continue
            except Exception:
                rewards = []

            return ReferralOut(
                id=m["id"],
                referrer_customer_id=m["referrer_customer_id"],
                code=m["code"],
                invitee_email=m.get("invitee_email"),
                invitee_phone=m.get("invitee_phone"),
                invitee_customer_id=m.get("invitee_customer_id"),
                status=m["status"],
                qualifying_order_id=m.get("qualifying_order_id"),
                invited_at=m.get("invited_at"),
                signed_up_at=m.get("signed_up_at"),
                qualified_at=m.get("qualified_at"),
                rewarded_at=m.get("rewarded_at"),
                rejected_at=m.get("rejected_at"),
                reversed_at=m.get("reversed_at"),
                rejection_reason=m.get("rejection_reason"),
                actor_id=m.get("actor_id"),
                created_at=m["created_at"],
                updated_at=m.get("updated_at") or m["created_at"],
                rewards=rewards,
            )
    except Exception:
        return None
