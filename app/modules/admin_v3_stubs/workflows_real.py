"""Real workflow engine for /admin/workflows (tables from migration 0098).

A definition has an ordered step list; each step is an "action" (executed
immediately, recorded succeeded) or a "gate" (pauses the run until an operator
resolves it via resolve-gate). Synchronous, in-process executor. All access is
raw SQL via the UnitOfWork session — no ORM models to register.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.db.uow import UnitOfWork


# ── reads ────────────────────────────────────────────────────────────────
async def list_definitions(uow: UnitOfWork) -> dict[str, Any]:
    async with uow.transactional() as s:
        rows = list((await s.execute(text(
            "SELECT code, name, description, steps, enabled, kill_switch "
            "FROM workflow_definitions ORDER BY code"
        ))).all())
    items = [
        {"code": r[0], "name": r[1], "description": r[2], "steps": r[3],
         "enabled": bool(r[4]), "kill_switch": bool(r[5])}
        for r in rows
    ]
    return {"items": items, "total": len(items)}


async def list_runs(uow: UnitOfWork, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    async with uow.transactional() as s:
        rows = list((await s.execute(text(
            "SELECT id, workflow_code, status, gate_step_index, started_at, finished_at "
            "FROM workflow_runs ORDER BY id DESC LIMIT :l"
        ), {"l": limit})).all())
    items = [
        {"id": int(r[0]), "workflow_code": r[1], "status": r[2],
         "gate_step_index": r[3],
         "started_at": r[4].isoformat() if r[4] else None,
         "finished_at": r[5].isoformat() if r[5] else None}
        for r in rows
    ]
    return {"items": items, "total": len(items)}


async def get_run(uow: UnitOfWork, run_id: int) -> dict[str, Any]:
    async with uow.transactional() as s:
        run = (await s.execute(text(
            "SELECT id, workflow_code, status, gate_step_index, trigger_payload, "
            "started_at, finished_at FROM workflow_runs WHERE id = :id"
        ), {"id": run_id})).one_or_none()
        if not run:
            return {"error": "run not found", "id": run_id}
        steps = list((await s.execute(text(
            "SELECT id, step_index, name, type, status, output, error "
            "FROM workflow_run_steps WHERE run_id = :id ORDER BY step_index"
        ), {"id": run_id})).all())
    return {
        "id": int(run[0]), "workflow_code": run[1], "status": run[2],
        "gate_step_index": run[3], "trigger_payload": run[4],
        "started_at": run[5].isoformat() if run[5] else None,
        "finished_at": run[6].isoformat() if run[6] else None,
        "steps": [
            {"id": int(s_[0]), "step_index": s_[1], "name": s_[2], "type": s_[3],
             "status": s_[4], "output": s_[5], "error": s_[6]}
            for s_ in steps
        ],
    }


# ── execution ────────────────────────────────────────────────────────────
async def _advance(s, run_id: int, from_index: int) -> tuple[str, int | None]:
    """Execute pending steps from ``from_index`` until done or a gate. Returns
    (run_status, gate_step_index|None). Caller holds the session/transaction."""
    steps = list((await s.execute(text(
        "SELECT id, step_index, type, status FROM workflow_run_steps "
        "WHERE run_id = :id AND step_index >= :i ORDER BY step_index"
    ), {"id": run_id, "i": from_index})).all())
    for st in steps:
        step_id, idx, stype, status = int(st[0]), st[1], st[2], st[3]
        if status in ("succeeded", "skipped"):
            continue
        if stype == "gate":
            await s.execute(text(
                "UPDATE workflow_run_steps SET status='awaiting_gate', "
                "started_at=now() WHERE id=:id"), {"id": step_id})
            return "awaiting_gate", idx
        # action: run immediately
        await s.execute(text(
            "UPDATE workflow_run_steps SET status='succeeded', started_at=now(), "
            "finished_at=now(), output=CAST(:o AS jsonb) WHERE id=:id"),
            {"id": step_id, "o": '{"ok": true}'})
    return "succeeded", None


async def trigger(uow: UnitOfWork, workflow_code: str, payload: dict[str, Any]) -> dict[str, Any]:
    import json
    async with uow.transactional() as s:
        d = (await s.execute(text(
            "SELECT steps, enabled, kill_switch FROM workflow_definitions WHERE code=:c"
        ), {"c": workflow_code})).one_or_none()
        if not d:
            return {"error": "unknown workflow_code", "workflow_code": workflow_code}
        steps_def, enabled, kill = d[0], bool(d[1]), bool(d[2])
        if kill or not enabled:
            return {"error": "workflow disabled or kill-switched",
                    "workflow_code": workflow_code, "status": "blocked"}
        run_id = int((await s.execute(text(
            "INSERT INTO workflow_runs (workflow_code, status, trigger_payload) "
            "VALUES (:c, 'running', CAST(:p AS jsonb)) RETURNING id"
        ), {"c": workflow_code, "p": json.dumps(payload or {})})).scalar_one())
        for i, step in enumerate(steps_def or []):
            await s.execute(text(
                "INSERT INTO workflow_run_steps (run_id, step_index, name, type, status) "
                "VALUES (:r, :i, :n, :t, 'pending')"),
                {"r": run_id, "i": i, "n": step.get("name", f"step{i}"),
                 "t": step.get("type", "action")})
        status, gate = await _advance(s, run_id, 0)
        await s.execute(text(
            "UPDATE workflow_runs SET status=:st, gate_step_index=:g, "
            "finished_at=CASE WHEN :done THEN now() ELSE NULL END WHERE id=:id"),
            {"st": status, "g": gate, "id": run_id, "done": status == "succeeded"})
    return await get_run(uow, run_id)


async def resolve_gate(uow: UnitOfWork, run_id: int, body: dict[str, Any]) -> dict[str, Any]:
    decision = str((body or {}).get("decision", "approve")).lower()
    async with uow.transactional() as s:
        run = (await s.execute(text(
            "SELECT status, gate_step_index FROM workflow_runs WHERE id=:id"
        ), {"id": run_id})).one_or_none()
        if not run:
            return {"error": "run not found", "id": run_id}
        if run[0] != "awaiting_gate" or run[1] is None:
            return {"error": "run is not awaiting a gate", "id": run_id, "status": run[0]}
        gate_idx = int(run[1])
        if decision in ("reject", "deny", "fail"):
            await s.execute(text(
                "UPDATE workflow_run_steps SET status='failed', finished_at=now(), "
                "error='gate rejected' WHERE run_id=:r AND step_index=:i"),
                {"r": run_id, "i": gate_idx})
            await s.execute(text(
                "UPDATE workflow_runs SET status='failed', finished_at=now() WHERE id=:id"),
                {"id": run_id})
        else:
            await s.execute(text(
                "UPDATE workflow_run_steps SET status='succeeded', finished_at=now(), "
                "output=CAST('{\"gate\":\"approved\"}' AS jsonb) "
                "WHERE run_id=:r AND step_index=:i"), {"r": run_id, "i": gate_idx})
            status, gate = await _advance(s, run_id, gate_idx + 1)
            await s.execute(text(
                "UPDATE workflow_runs SET status=:st, gate_step_index=:g, "
                "finished_at=CASE WHEN :done THEN now() ELSE NULL END WHERE id=:id"),
                {"st": status, "g": gate, "id": run_id, "done": status == "succeeded"})
    return await get_run(uow, run_id)


async def retry_step(uow: UnitOfWork, run_id: int, step_id: int) -> dict[str, Any]:
    async with uow.transactional() as s:
        st = (await s.execute(text(
            "SELECT step_index, type FROM workflow_run_steps WHERE id=:id AND run_id=:r"
        ), {"id": step_id, "r": run_id})).one_or_none()
        if not st:
            return {"error": "step not found", "run_id": run_id, "step_id": step_id}
        await s.execute(text(
            "UPDATE workflow_run_steps SET status='pending', error=NULL, "
            "output=NULL, finished_at=NULL WHERE id=:id"), {"id": step_id})
        status, gate = await _advance(s, run_id, int(st[0]))
        await s.execute(text(
            "UPDATE workflow_runs SET status=:st, gate_step_index=:g, "
            "finished_at=CASE WHEN :done THEN now() ELSE NULL END WHERE id=:id"),
            {"st": status, "g": gate, "id": run_id, "done": status == "succeeded"})
    return await get_run(uow, run_id)


async def kill_switch(uow: UnitOfWork, workflow_code: str, enabled: bool) -> dict[str, Any]:
    async with uow.transactional() as s:
        res = (await s.execute(text(
            "UPDATE workflow_definitions SET kill_switch=:k, updated_at=now() "
            "WHERE code=:c RETURNING code, kill_switch"
        ), {"k": bool(enabled), "c": workflow_code})).one_or_none()
    if not res:
        return {"error": "unknown workflow_code", "workflow_code": workflow_code}
    return {"workflow_code": res[0], "kill_switch": bool(res[1])}
