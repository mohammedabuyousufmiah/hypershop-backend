"""Gap-fill admin fraud endpoints for the AdminGrowthClient "fraud" tab.

These three GET endpoints back the frontend ``api.fraudAdmin`` calls that the
existing ``admin.py`` router does not serve:

  * GET /admin/fraud/signals          -> FraudSignalPageWire   {items, next_cursor}
  * GET /admin/fraud/velocity         -> FraudVelocityCounterWire
  * GET /admin/fraud/assessments/{id} -> FraudAssessmentWire

The frontend wire shapes (``@ecom/types``) describe a richer fraud model than
this build's ``fraud_assessments`` table actually has. There are *no* tables
backing per-actor signals or velocity counters, so those endpoints return
correctly-shaped EMPTY payloads — the FE renders a calm empty state instead of
a 404. The assessment-detail endpoint reads the real ``fraud_assessments`` row
defensively (raw SQL, try/except) and maps the columns that exist onto the FE
shape; a genuinely missing row returns 404 (the FE degrades gracefully on 404).

Every query is wrapped so a missing/renamed table or column yields an empty
response rather than a 500. The module imports only stable core symbols so it
is import-safe and boot-safe on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/admin/fraud", tags=["admin-fraud-extra"])

# Admins reliably hold dashboard.read (the sibling admin.py read-gate).
_R = "dashboard.read"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get(
    "/signals",
    dependencies=[Depends(requires_permission(_R))],
)
async def list_fraud_signals(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor_id: str = Query(...),
    signal_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Risk-bearing signals for an actor.

    No ``fraud_signals`` table exists in this build, so this returns a
    correctly-shaped empty page (FraudSignalPageWire). The query is attempted
    defensively in case a table is added later; any error -> empty page.
    """
    items: list[dict[str, Any]] = []
    try:
        async with uow.transactional() as session:
            exists = await session.execute(
                text("SELECT to_regclass('public.fraud_signals') AS t")
            )
            if exists.scalar() is not None:
                where = "WHERE actor_id = :actor_id"
                params: dict[str, Any] = {"actor_id": actor_id, "limit": limit}
                if signal_type:
                    where += " AND signal_type = :signal_type"
                    params["signal_type"] = signal_type
                rows = await session.execute(
                    text(
                        "SELECT id, signal_type, actor_id, actor_role, "
                        "ip_address, device_fingerprint, user_agent, payload, "
                        "recorded_at, created_at "
                        f"FROM fraud_signals {where} "
                        "ORDER BY created_at DESC LIMIT :limit"
                    ),
                    params,
                )
                for r in rows.mappings():
                    items.append(
                        {
                            "id": str(r.get("id")),
                            "signal_type": r.get("signal_type"),
                            "actor_id": str(r.get("actor_id")),
                            "actor_role": r.get("actor_role"),
                            "ip_address": r.get("ip_address"),
                            "device_fingerprint": r.get("device_fingerprint"),
                            "user_agent": r.get("user_agent"),
                            "payload": r.get("payload") or {},
                            "recorded_at": _iso(r.get("recorded_at")),
                            "created_at": _iso(r.get("created_at")),
                        }
                    )
    except Exception:
        items = []
    return {"items": items, "next_cursor": None}


@router.get(
    "/velocity",
    dependencies=[Depends(requires_permission(_R))],
)
async def get_fraud_velocity(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    bucket_key: str = Query(...),
    window_seconds: int = Query(default=3600, ge=1),
) -> dict[str, Any]:
    """Velocity counter snapshot for a bucket key.

    No ``fraud_velocity_counters`` table exists in this build, so this returns
    a correctly-shaped zeroed counter (FraudVelocityCounterWire). Attempted
    defensively in case a counter table is added later.
    """
    now = datetime.now(timezone.utc)
    count = 0
    window_start = now - timedelta(seconds=window_seconds)
    expires_at = window_start + timedelta(seconds=window_seconds)
    try:
        async with uow.transactional() as session:
            exists = await session.execute(
                text("SELECT to_regclass('public.fraud_velocity_counters') AS t")
            )
            if exists.scalar() is not None:
                row = await session.execute(
                    text(
                        "SELECT count, window_start, expires_at "
                        "FROM fraud_velocity_counters "
                        "WHERE bucket_key = :bucket_key "
                        "AND window_seconds = :window_seconds "
                        "ORDER BY window_start DESC LIMIT 1"
                    ),
                    {"bucket_key": bucket_key, "window_seconds": window_seconds},
                )
                m = row.mappings().first()
                if m is not None:
                    count = int(m.get("count") or 0)
                    if m.get("window_start") is not None:
                        window_start = m.get("window_start")
                    if m.get("expires_at") is not None:
                        expires_at = m.get("expires_at")
    except Exception:
        count = 0
    return {
        "bucket_key": bucket_key,
        "window_seconds": window_seconds,
        "window_start": _iso(window_start),
        "expires_at": _iso(expires_at),
        "count": count,
    }


@router.get(
    "/assessments/{assessment_id}",
    dependencies=[Depends(requires_permission(_R))],
)
async def get_fraud_assessment(
    assessment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    """Single assessment detail, mapped onto the FE FraudAssessmentWire shape.

    Reads the real ``fraud_assessments`` row defensively. This build's table
    has a leaner column set (subject_type/subject_id/risk_score/decision/
    signals/decision_reason/decided_by/created_at) than the FE wire, so the
    extra wire fields (action/level/factors/amount/...) are filled with safe
    defaults derived from what exists. A missing row -> 404 (FE handles it).
    """
    found: dict[str, Any] | None = None
    try:
        async with uow.transactional() as session:
            exists = await session.execute(
                text("SELECT to_regclass('public.fraud_assessments') AS t")
            )
            if exists.scalar() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Assessment not found",
                )
            row = await session.execute(
                text("SELECT * FROM fraud_assessments WHERE id = :id"),
                {"id": str(assessment_id)},
            )
            found = dict(row.mappings().first() or {}) or None
    except HTTPException:
        raise
    except Exception:
        found = None

    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assessment not found",
        )

    # Map this build's lean decision vocabulary onto the FE decision enum and
    # derive a coarse risk level from risk_score when no level column exists.
    raw_decision = str(found.get("decision") or "").upper()
    decision_map = {
        "CLEAR": "APPROVED",
        "BLOCK": "DECLINED",
        "CHALLENGE": "CHALLENGED",
    }
    decision = decision_map.get(raw_decision, raw_decision or "PENDING")
    if decision not in {"PENDING", "APPROVED", "DECLINED", "CHALLENGED"}:
        decision = "PENDING"

    score = found.get("risk_score")
    score_val = None
    try:
        score_val = int(score) if score is not None else None
    except (TypeError, ValueError):
        score_val = None

    if score_val is None:
        level = "LOW"
    elif score_val >= 90:
        level = "CRITICAL"
    elif score_val >= 70:
        level = "HIGH"
    elif score_val >= 40:
        level = "MEDIUM"
    else:
        level = "LOW"

    signals = found.get("signals")
    factors = signals if isinstance(signals, list) else []

    subject_ref = ""
    if found.get("subject_type") is not None or found.get("subject_id") is not None:
        subject_ref = f"{found.get('subject_type') or ''}:{found.get('subject_id') or ''}"

    return {
        "id": str(found.get("id")),
        "action": (found.get("action") or "CHECKOUT"),
        "actor_id": str(found.get("subject_id") or found.get("actor_id") or ""),
        "actor_role": found.get("actor_role"),
        "subject_ref": subject_ref,
        "ip_address": found.get("ip_address"),
        "device_fingerprint": found.get("device_fingerprint"),
        "score": score_val,
        "level": level,
        "decision": decision,
        "factors": factors,
        "reason": found.get("decision_reason"),
        "decided_by": (
            str(found.get("decided_by")) if found.get("decided_by") else None
        ),
        "decided_at": _iso(found.get("decided_at")),
        "amount": (str(found.get("amount")) if found.get("amount") is not None else None),
        "currency": found.get("currency"),
        "created_at": _iso(found.get("created_at")),
        "updated_at": _iso(found.get("updated_at") or found.get("created_at")),
    }
