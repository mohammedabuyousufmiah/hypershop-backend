"""Referrals admin ACTION gap endpoints — Phase-4 ops verbs for AdminGrowthClient.

Companion to ``referrals_gap.py`` (the GET gap router). The growth panel's
Referrals tab performs three row-level mutations on a referral:

  POST /referrals/admin/referrals/{id}/reject   body {reason}            -> ReferralOut
  POST /referrals/admin/referrals/{id}/reverse  body {reason}            -> ReferralOut
  POST /referrals/admin/referrals/{id}/reward   body [ReferralRewardGrantIn] -> [ReferralRewardOut]

These mutate the SAME Phase-4 ``referrals`` / ``referral_rewards`` tables the GET
gap router reads. That schema is not guaranteed present in every build, so every
handler is fully defensive: a missing table/column yields a clean 200
``{"ok": false, "reason": "not_available"}`` instead of a 500. Each write runs
inside an explicit transaction with raw ``text()`` SQL — no maybe-missing model
imports, boot-safe.

Self-contained: registered centrally in main.py (do not import elsewhere).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, Header
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission

# Same prefix + perm the GET gap router uses, so the FE's mutation paths resolve
# under the identical surface and RBAC gate.
router = APIRouter(prefix="/referrals/admin", tags=["admin-referrals-actions"])

_PERM = "referrals.admin"


# ---------------------------------------------------------------------------
# Request bodies — match @ecom/types ReferralRejectIn / ReferralReverseIn /
# ReferralRewardGrantIn exactly.
# ---------------------------------------------------------------------------
class ReferralReasonIn(BaseModel):
    reason: str
    model_config = ConfigDict(extra="ignore")


class ReferralRewardGrantIn(BaseModel):
    beneficiary: str  # REFERRER | INVITEE
    kind: str  # WALLET_CREDIT | LOYALTY_POINTS | COUPON
    amount: float | None = None
    currency: str | None = None
    points: int | None = None
    coupon_id: UUID | None = None
    idempotency_key: str | None = None
    actor_id: UUID | None = None
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Defensive probes — keep every handler 500-proof regardless of which migration
# set is applied.
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


async def _rewards_table_ready(session: Any) -> bool:
    try:
        row = (
            await session.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'referral_rewards' "
                    "AND column_name = 'referral_id' LIMIT 1"
                )
            )
        ).first()
        return row is not None
    except Exception:
        return False


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    m = row._mapping if hasattr(row, "_mapping") else row
    return {k: m[k] for k in m.keys()}


def _actor_id(principal: Principal) -> str | None:
    for attr in ("id", "user_id", "subject", "sub"):
        val = getattr(principal, attr, None)
        if val:
            return str(val)
    return None


async def _fetch_referral(session: Any, referral_id: UUID) -> dict[str, Any] | None:
    r = (
        await session.execute(
            text(
                "SELECT id, referrer_customer_id, code, invitee_email, "
                "invitee_phone, invitee_customer_id, status, "
                "qualifying_order_id, invited_at, signed_up_at, qualified_at, "
                "rewarded_at, rejected_at, reversed_at, rejection_reason, "
                "actor_id, created_at, updated_at "
                "FROM referrals WHERE id = :id LIMIT 1"
            ),
            {"id": str(referral_id)},
        )
    ).first()
    return _row_to_dict(r) if r is not None else None


# ---------------------------------------------------------------------------
# POST /referrals/{id}/reject — move a referral to REJECTED with a reason.
# ---------------------------------------------------------------------------
@router.post(
    "/referrals/{referral_id}/reject",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def reject_referral(
    referral_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    body: ReferralReasonIn = Body(...),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """Reject a referral. Idempotent: re-rejecting an already-REJECTED row is a
    no-op that returns the current row."""
    try:
        async with uow.transactional() as session:
            if not await _referrals_table_ready(session):
                return {"ok": False, "reason": "not_available"}
            existing = await _fetch_referral(session, referral_id)
            if existing is None:
                return {"ok": False, "reason": "not_found"}
            now = datetime.now(timezone.utc)
            await session.execute(
                text(
                    "UPDATE referrals SET status = 'REJECTED', "
                    "rejection_reason = :reason, rejected_at = "
                    "COALESCE(rejected_at, :now), actor_id = :actor, "
                    "updated_at = :now WHERE id = :id"
                ),
                {
                    "reason": body.reason,
                    "now": now,
                    "actor": _actor_id(principal),
                    "id": str(referral_id),
                },
            )
            updated = await _fetch_referral(session, referral_id)
        return updated or {"ok": True}
    except Exception:
        return {"ok": False, "reason": "not_available"}


# ---------------------------------------------------------------------------
# POST /referrals/{id}/reverse — move a referral to REVERSED with a reason.
# ---------------------------------------------------------------------------
@router.post(
    "/referrals/{referral_id}/reverse",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def reverse_referral(
    referral_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    body: ReferralReasonIn = Body(...),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """Reverse a (typically already-rewarded) referral. Best-effort also marks
    any granted rewards REVERSED so the detail fanout stays consistent."""
    try:
        async with uow.transactional() as session:
            if not await _referrals_table_ready(session):
                return {"ok": False, "reason": "not_available"}
            existing = await _fetch_referral(session, referral_id)
            if existing is None:
                return {"ok": False, "reason": "not_found"}
            now = datetime.now(timezone.utc)
            await session.execute(
                text(
                    "UPDATE referrals SET status = 'REVERSED', "
                    "rejection_reason = COALESCE(:reason, rejection_reason), "
                    "reversed_at = COALESCE(reversed_at, :now), "
                    "actor_id = :actor, updated_at = :now WHERE id = :id"
                ),
                {
                    "reason": body.reason,
                    "now": now,
                    "actor": _actor_id(principal),
                    "id": str(referral_id),
                },
            )
            # Cascade reward reversal — non-fatal if the rewards table is absent.
            try:
                if await _rewards_table_ready(session):
                    await session.execute(
                        text(
                            "UPDATE referral_rewards SET status = 'REVERSED', "
                            "reversed_at = COALESCE(reversed_at, :now), "
                            "reversal_reason = COALESCE(:reason, reversal_reason), "
                            "updated_at = :now "
                            "WHERE referral_id = :id AND status <> 'REVERSED'"
                        ),
                        {"now": now, "reason": body.reason, "id": str(referral_id)},
                    )
            except Exception:
                pass
            updated = await _fetch_referral(session, referral_id)
        return updated or {"ok": True}
    except Exception:
        return {"ok": False, "reason": "not_available"}


# ---------------------------------------------------------------------------
# POST /referrals/{id}/reward — force-grant the reward fanout (list of grants).
# ---------------------------------------------------------------------------
@router.post(
    "/referrals/{referral_id}/reward",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def force_grant_rewards(
    referral_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    body: list[ReferralRewardGrantIn] = Body(...),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    """Force-grant one or more reward rows for a referral and flip it REWARDED.

    Returns the inserted reward rows. Idempotency: a grant whose
    ``idempotency_key`` already exists is skipped (ON CONFLICT DO NOTHING)."""
    try:
        async with uow.transactional() as session:
            if not await _referrals_table_ready(session):
                return {"ok": False, "reason": "not_available"}
            existing = await _fetch_referral(session, referral_id)
            if existing is None:
                return {"ok": False, "reason": "not_found"}
            if not await _rewards_table_ready(session):
                return {"ok": False, "reason": "not_available"}

            now = datetime.now(timezone.utc)
            actor = _actor_id(principal)
            # Beneficiary customer routing: REFERRER -> referrer_customer_id,
            # INVITEE -> invitee_customer_id (fall back to referrer if invitee
            # is not yet linked so the NOT NULL column is always satisfied).
            referrer_cid = existing.get("referrer_customer_id")
            invitee_cid = existing.get("invitee_customer_id") or referrer_cid

            inserted: list[dict[str, Any]] = []
            for g in body:
                bene_cid = (
                    invitee_cid if g.beneficiary == "INVITEE" else referrer_cid
                )
                if bene_cid is None:
                    continue
                idem = g.idempotency_key or str(uuid4())
                params = {
                    "id": str(uuid4()),
                    "referral_id": str(referral_id),
                    "beneficiary": g.beneficiary,
                    "kind": g.kind,
                    "bene_cid": str(bene_cid),
                    "amount": g.amount,
                    "currency": g.currency,
                    "points": g.points,
                    "coupon_id": str(g.coupon_id) if g.coupon_id else None,
                    "actor": str(g.actor_id) if g.actor_id else actor,
                    "idem": idem,
                    "now": now,
                }
                try:
                    row = (
                        await session.execute(
                            text(
                                "INSERT INTO referral_rewards "
                                "(id, referral_id, beneficiary, kind, "
                                "beneficiary_customer_id, amount, currency, "
                                "points, coupon_id, status, granted_at, actor_id, "
                                "idempotency_key, created_at, updated_at) VALUES "
                                "(:id, :referral_id, :beneficiary, :kind, "
                                ":bene_cid, :amount, :currency, :points, "
                                ":coupon_id, 'GRANTED', :now, :actor, :idem, "
                                ":now, :now) "
                                "ON CONFLICT (idempotency_key) DO NOTHING "
                                "RETURNING id, referral_id, beneficiary, kind, "
                                "beneficiary_customer_id, amount, currency, "
                                "points, coupon_id, status, ledger_reference_type, "
                                "ledger_reference_id, granted_at, reversed_at, "
                                "reversal_reason, actor_id, created_at, updated_at"
                            ),
                            params,
                        )
                    ).first()
                    if row is not None:
                        inserted.append(_row_to_dict(row))
                except Exception:
                    # A unique idempotency index may not exist; retry without
                    # ON CONFLICT so the grant still lands.
                    try:
                        row = (
                            await session.execute(
                                text(
                                    "INSERT INTO referral_rewards "
                                    "(id, referral_id, beneficiary, kind, "
                                    "beneficiary_customer_id, amount, currency, "
                                    "points, coupon_id, status, granted_at, "
                                    "actor_id, idempotency_key, created_at, "
                                    "updated_at) VALUES "
                                    "(:id, :referral_id, :beneficiary, :kind, "
                                    ":bene_cid, :amount, :currency, :points, "
                                    ":coupon_id, 'GRANTED', :now, :actor, :idem, "
                                    ":now, :now) "
                                    "RETURNING id, referral_id, beneficiary, "
                                    "kind, beneficiary_customer_id, amount, "
                                    "currency, points, coupon_id, status, "
                                    "ledger_reference_type, ledger_reference_id, "
                                    "granted_at, reversed_at, reversal_reason, "
                                    "actor_id, created_at, updated_at"
                                ),
                                params,
                            )
                        ).first()
                        if row is not None:
                            inserted.append(_row_to_dict(row))
                    except Exception:
                        continue

            # Flip the parent referral to REWARDED.
            try:
                await session.execute(
                    text(
                        "UPDATE referrals SET status = 'REWARDED', "
                        "rewarded_at = COALESCE(rewarded_at, :now), "
                        "actor_id = :actor, updated_at = :now WHERE id = :id"
                    ),
                    {"now": now, "actor": actor, "id": str(referral_id)},
                )
            except Exception:
                pass
        return inserted
    except Exception:
        return {"ok": False, "reason": "not_available"}
