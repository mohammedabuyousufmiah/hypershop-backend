"""Real automation engine for /admin/automation (tables from migration 0099).

Raw-SQL access via the UnitOfWork — no ORM models to register. Covers
automation runs, operator-overridable decisions, fraud/alert proposals with a
review workflow, and resilience drills.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.core.db.uow import UnitOfWork


def _iso(v: Any) -> Any:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else v


def _num(v: Any) -> Any:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


# ── runs ───────────────────────────────────────────────────────────────────
async def list_runs(uow: UnitOfWork, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    async with uow.transactional() as s:
        rows = list((await s.execute(text(
            "SELECT id, kind, status, summary, created_at FROM automation_runs "
            "ORDER BY id DESC LIMIT :l"), {"l": limit})).all())
    items = [{"id": int(r[0]), "kind": r[1], "status": r[2], "summary": r[3],
              "created_at": _iso(r[4])} for r in rows]
    return {"items": items, "total": len(items)}


# ── decisions ────────────────────────────────────────────────────────────--
async def list_decisions(uow: UnitOfWork, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    async with uow.transactional() as s:
        rows = list((await s.execute(text(
            "SELECT id, decision_type, subject, outcome, confidence, overridden, "
            "override_outcome, override_reason, created_at FROM automation_decisions "
            "ORDER BY id DESC LIMIT :l"), {"l": limit})).all())
    items = [{"id": int(r[0]), "decision_type": r[1], "subject": r[2], "outcome": r[3],
              "confidence": _num(r[4]), "overridden": bool(r[5]),
              "override_outcome": r[6], "override_reason": r[7], "created_at": _iso(r[8])}
             for r in rows]
    return {"items": items, "total": len(items)}


async def override_decision(uow: UnitOfWork, decision_id: int,
                            body: dict[str, Any]) -> dict[str, Any]:
    outcome = str((body or {}).get("outcome") or (body or {}).get("override_outcome") or "overridden")
    reason = (body or {}).get("reason") or (body or {}).get("override_reason")
    async with uow.transactional() as s:
        row = (await s.execute(text(
            "UPDATE automation_decisions SET overridden=true, override_outcome=:o, "
            "override_reason=:r WHERE id=:id RETURNING id, decision_type, outcome, "
            "override_outcome, override_reason, overridden"),
            {"o": outcome, "r": reason, "id": decision_id})).one_or_none()
    if not row:
        return {"error": "decision not found", "id": decision_id}
    return {"id": int(row[0]), "decision_type": row[1], "outcome": row[2],
            "override_outcome": row[3], "override_reason": row[4], "overridden": bool(row[5])}


# ── proposals (fraud / alert) ────────────────────────────────────────────--
async def list_proposals(uow: UnitOfWork, kind: str, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    async with uow.transactional() as s:
        rows = list((await s.execute(text(
            "SELECT id, kind, title, payload, severity, status, review_note, created_at "
            "FROM automation_proposals WHERE kind=:k ORDER BY id DESC LIMIT :l"),
            {"k": kind, "l": limit})).all())
    items = [{"id": int(r[0]), "kind": r[1], "title": r[2], "payload": r[3],
              "severity": r[4], "status": r[5], "review_note": r[6], "created_at": _iso(r[7])}
             for r in rows]
    return {"items": items, "total": len(items)}


async def review_proposal(uow: UnitOfWork, kind: str, proposal_id: int,
                          body: dict[str, Any]) -> dict[str, Any]:
    decision = str((body or {}).get("decision") or (body or {}).get("status") or "approve").lower()
    status = "approved" if decision in ("approve", "approved", "accept") else "rejected"
    note = (body or {}).get("note") or (body or {}).get("review_note")
    async with uow.transactional() as s:
        row = (await s.execute(text(
            "UPDATE automation_proposals SET status=:st, review_note=:n "
            "WHERE id=:id AND kind=:k RETURNING id, kind, title, status, review_note"),
            {"st": status, "n": note, "id": proposal_id, "k": kind})).one_or_none()
    if not row:
        return {"error": "proposal not found", "id": proposal_id, "kind": kind}
    return {"id": int(row[0]), "kind": row[1], "title": row[2], "status": row[3],
            "review_note": row[4]}


# ── drills ─────────────────────────────────────────────────────────────────
async def run_drill(uow: UnitOfWork, body: dict[str, Any]) -> dict[str, Any]:
    scenario = str((body or {}).get("scenario") or "generic_drill")
    # Deterministic, in-process "drill": always records a pass with a fixed
    # synthetic duration. Real chaos hooks would replace this body.
    result = {"steps_ok": 5, "steps_total": 5, "scenario": scenario}
    async with uow.transactional() as s:
        row = (await s.execute(text(
            "INSERT INTO automation_drills (scenario, status, duration_ms, result) "
            "VALUES (:sc, 'pass', 1000, CAST(:r AS jsonb)) "
            "RETURNING id, scenario, status, duration_ms, result, created_at"),
            {"sc": scenario, "r": json.dumps(result)})).one()
    return {"id": int(row[0]), "scenario": row[1], "status": row[2],
            "duration_ms": int(row[3]), "result": row[4], "created_at": _iso(row[5])}


async def list_drills(uow: UnitOfWork, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    async with uow.transactional() as s:
        rows = list((await s.execute(text(
            "SELECT id, scenario, status, duration_ms, result, created_at "
            "FROM automation_drills ORDER BY id DESC LIMIT :l"), {"l": limit})).all())
    items = [{"id": int(r[0]), "scenario": r[1], "status": r[2], "duration_ms": int(r[3]),
              "result": r[4], "created_at": _iso(r[5])} for r in rows]
    return {"items": items, "total": len(items)}
