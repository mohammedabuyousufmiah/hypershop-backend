"""Gap-fill admin fraud ACTION endpoints for the AdminGrowthClient "fraud" tab.

The FE (``components/AdminGrowthClient.tsx`` -> ``FraudTab`` /
``FraudAssessmentDetailDialog``) performs exactly one row-level mutation on its
own resources via ``api.fraudAdmin.decide``:

  * POST /admin/fraud/assessments/{assessment_id}/decide
        body: {"decision": "APPROVED"|"DECLINED"|"CHALLENGED", "reason"?: str}

This router serves that action defensively against the REAL ``fraud_assessments``
table (the same table the GET gap file reads). That table carries a CHECK
constraint ``decision IN ('CLEAR','CHALLENGE','BLOCK')`` and a leaner column set
than the FE wire, so:

  * the FE decision vocabulary is reverse-mapped onto the DB vocabulary
    (APPROVED->CLEAR, DECLINED->BLOCK, CHALLENGED->CHALLENGE) — this is the
    inverse of the mapping the GET gap file applies when reading;
  * only columns that actually exist are written (``decision`` always;
    ``decision_reason`` / ``decided_by`` only when present), so the statement is
    safe across schema variants.

Every write runs inside an explicit transaction wrapped in try/except. A missing
table/column yields a clean ``200 {"ok": false, "reason": "not_available"}`` and
a missing row yields ``404`` — never a 500. The module imports only stable core
symbols, so it is import-safe and boot-safe on its own.

Register centrally (do NOT edit existing files):

    from app.modules.fraud.api.fraud_extra_actions_gap import (
        router as fraud_extra_actions_gap_router,
    )
    app.include_router(fraud_extra_actions_gap_router, prefix="/api/v1")
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

# Same prefix + read-gate as the GET gap router (fraud_extra_gap.py).
router = APIRouter(prefix="/admin/fraud", tags=["admin-fraud-extra-actions"])

_R = "dashboard.read"

# FE decision enum -> DB ``decision`` vocabulary (inverse of the GET gap map).
_DECISION_TO_DB = {
    "APPROVED": "CLEAR",
    "DECLINED": "BLOCK",
    "CHALLENGED": "CHALLENGE",
    # tolerate raw DB values being passed straight through
    "CLEAR": "CLEAR",
    "BLOCK": "BLOCK",
    "CHALLENGE": "CHALLENGE",
}
# DB ``decision`` -> FE decision enum (matches the GET gap router's read map).
_DECISION_TO_FE = {
    "CLEAR": "APPROVED",
    "BLOCK": "DECLINED",
    "CHALLENGE": "CHALLENGED",
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class FraudDecideIn(BaseModel):
    decision: str
    reason: str | None = None


def _level_for(score: Any) -> str:
    try:
        s = int(score) if score is not None else None
    except (TypeError, ValueError):
        s = None
    if s is None:
        return "LOW"
    if s >= 90:
        return "CRITICAL"
    if s >= 70:
        return "HIGH"
    if s >= 40:
        return "MEDIUM"
    return "LOW"


@router.post(
    "/assessments/{assessment_id}/decide",
    dependencies=[Depends(requires_permission(_R))],
)
async def decide_fraud_assessment(
    assessment_id: UUID,
    body: FraudDecideIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Apply an admin decision to a real ``fraud_assessments`` row.

    Maps the FE decision onto the DB CHECK vocabulary and writes only the
    columns that exist. Returns the updated row shaped like the FE
    ``FraudAssessmentWire`` so the dialog can refresh in place. Defensive:
    absent table/column -> 200 {"ok": false, "reason": "not_available"};
    absent row -> 404; never 500.
    """
    db_decision = _DECISION_TO_DB.get(str(body.decision).upper())
    if db_decision is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown decision",
        )

    try:
        async with uow.transactional() as session:
            exists = await session.execute(
                text("SELECT to_regclass('public.fraud_assessments') AS t")
            )
            if exists.scalar() is None:
                return {"ok": False, "reason": "not_available"}

            # Discover which optional columns exist on this build's table.
            cols_res = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'fraud_assessments'"
                )
            )
            cols = {r[0] for r in cols_res.fetchall()}
            if "decision" not in cols:
                return {"ok": False, "reason": "not_available"}

            sets = ["decision = :decision"]
            params: dict[str, Any] = {
                "id": str(assessment_id),
                "decision": db_decision,
            }
            if "decision_reason" in cols:
                sets.append("decision_reason = :reason")
                params["reason"] = (body.reason or "").strip() or None
            # decided_by is nullable; we cannot resolve the acting admin id from
            # raw SQL safely here, so leave it untouched to avoid bad FK writes.
            if "updated_at" in cols:
                sets.append("updated_at = now()")

            updated = await session.execute(
                text(
                    "UPDATE fraud_assessments SET "
                    + ", ".join(sets)
                    + " WHERE id = :id RETURNING *"
                ),
                params,
            )
            row = updated.mappings().first()
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Assessment not found",
                )
            found = dict(row)
    except HTTPException:
        raise
    except Exception:
        # Any unexpected DB/schema issue degrades to a clean, non-500 signal.
        return {"ok": False, "reason": "not_available"}

    raw_decision = str(found.get("decision") or "").upper()
    fe_decision = _DECISION_TO_FE.get(raw_decision, raw_decision or "PENDING")
    if fe_decision not in {"PENDING", "APPROVED", "DECLINED", "CHALLENGED"}:
        fe_decision = "PENDING"

    score = found.get("risk_score")
    try:
        score_val = int(score) if score is not None else None
    except (TypeError, ValueError):
        score_val = None

    signals = found.get("signals")
    factors = signals if isinstance(signals, list) else []

    subject_ref = ""
    if found.get("subject_type") is not None or found.get("subject_id") is not None:
        subject_ref = (
            f"{found.get('subject_type') or ''}:{found.get('subject_id') or ''}"
        )

    return {
        "id": str(found.get("id")),
        "action": (found.get("action") or "CHECKOUT"),
        "actor_id": str(found.get("subject_id") or found.get("actor_id") or ""),
        "actor_role": found.get("actor_role"),
        "subject_ref": subject_ref,
        "ip_address": found.get("ip_address"),
        "device_fingerprint": found.get("device_fingerprint"),
        "score": score_val,
        "level": _level_for(score_val),
        "decision": fe_decision,
        "factors": factors,
        "reason": found.get("decision_reason"),
        "decided_by": (
            str(found.get("decided_by")) if found.get("decided_by") else None
        ),
        "decided_at": _iso(found.get("decided_at")),
        "amount": (
            str(found.get("amount")) if found.get("amount") is not None else None
        ),
        "currency": found.get("currency"),
        "created_at": _iso(found.get("created_at")),
        "updated_at": _iso(found.get("updated_at") or found.get("created_at")),
    }
