"""Generic persistence for the realized admin-v3 modules.

All previously-stubbed resources (security secrets, order-trust blacklist,
seller reserves, rider wallet/settlement records, BI cohorts, ad campaigns,
etc.) are persisted as rows in a single ``av3_records`` table, partitioned by
``kind``. This gives every endpoint real, queryable persistence and real
status transitions without a bespoke table per resource.

Row shape: id (bigserial), kind, ref (optional owner key), status, payload
(JSONB body), created_at, updated_at.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.core.db.uow import UnitOfWork


def _row_out(r: Any) -> dict[str, Any]:
    payload = r[4] if isinstance(r[4], dict) else {}
    return {
        "id": int(r[0]), "kind": r[1], "ref": r[2], "status": r[3],
        "created_at": r[5].isoformat() if r[5] else None,
        "updated_at": r[6].isoformat() if r[6] else None,
        **payload,
    }


_SEL = "id, kind, ref, status, payload, created_at, updated_at"


async def create(uow: UnitOfWork, kind: str, payload: dict[str, Any] | None = None,
                 ref: str | None = None, status: str = "active") -> dict[str, Any]:
    payload = {k: v for k, v in (payload or {}).items()
               if k not in ("ref", "status", "kind", "id")}
    async with uow.transactional() as s:
        row = (await s.execute(text(
            f"INSERT INTO av3_records (kind, ref, status, payload) "
            f"VALUES (:k, :r, :st, CAST(:p AS jsonb)) RETURNING {_SEL}"),
            {"k": kind, "r": (str(ref) if ref is not None else None),
             "st": status, "p": json.dumps(payload)})).one()
    return _row_out(row)


async def listing(uow: UnitOfWork, kind: str, ref: str | None = None,
                  status: str | None = None, limit: int = 200) -> dict[str, Any]:
    limit = max(1, min(int(limit or 200), 500))
    clauses = ["kind = :k"]
    params: dict[str, Any] = {"k": kind, "l": limit}
    if ref is not None:
        clauses.append("ref = :r"); params["r"] = str(ref)
    if status is not None:
        clauses.append("status = :st"); params["st"] = status
    async with uow.transactional() as s:
        rows = list((await s.execute(text(
            f"SELECT {_SEL} FROM av3_records WHERE {' AND '.join(clauses)} "
            f"ORDER BY id DESC LIMIT :l"), params)).all())
    items = [_row_out(r) for r in rows]
    return {"items": items, "total": len(items)}


async def get(uow: UnitOfWork, kind: str, rid: int) -> dict[str, Any] | None:
    async with uow.transactional() as s:
        row = (await s.execute(text(
            f"SELECT {_SEL} FROM av3_records WHERE kind=:k AND id=:id"),
            {"k": kind, "id": rid})).one_or_none()
    return _row_out(row) if row else None


async def get_by_ref(uow: UnitOfWork, kind: str, ref: str) -> dict[str, Any] | None:
    async with uow.transactional() as s:
        row = (await s.execute(text(
            f"SELECT {_SEL} FROM av3_records WHERE kind=:k AND ref=:r "
            f"ORDER BY id DESC LIMIT 1"), {"k": kind, "r": str(ref)})).one_or_none()
    return _row_out(row) if row else None


async def patch(uow: UnitOfWork, kind: str, rid: int,
                updates: dict[str, Any] | None = None,
                status: str | None = None) -> dict[str, Any] | None:
    updates = {k: v for k, v in (updates or {}).items()
              if k not in ("ref", "status", "kind", "id")}
    async with uow.transactional() as s:
        row = (await s.execute(text(
            f"UPDATE av3_records SET payload = payload || CAST(:p AS jsonb), "
            f"status = COALESCE(:st, status), updated_at = now() "
            f"WHERE kind=:k AND id=:id RETURNING {_SEL}"),
            {"k": kind, "id": rid, "p": json.dumps(updates), "st": status})).one_or_none()
    return _row_out(row) if row else None


async def set_status(uow: UnitOfWork, kind: str, rid: int, status: str,
                     extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
    return await patch(uow, kind, rid, updates=extra, status=status)


async def remove(uow: UnitOfWork, kind: str, rid: int) -> bool:
    async with uow.transactional() as s:
        res = await s.execute(text(
            "DELETE FROM av3_records WHERE kind=:k AND id=:id"), {"k": kind, "id": rid})
    return (res.rowcount or 0) > 0


async def upsert_singleton(uow: UnitOfWork, kind: str,
                           payload: dict[str, Any]) -> dict[str, Any]:
    existing = await get_by_ref(uow, kind, "_singleton")
    if existing:
        return await patch(uow, kind, existing["id"], updates=payload) or existing
    return await create(uow, kind, payload=payload, ref="_singleton")


async def get_singleton(uow: UnitOfWork, kind: str,
                        default: dict[str, Any]) -> dict[str, Any]:
    existing = await get_by_ref(uow, kind, "_singleton")
    return existing if existing else {"kind": kind, **default}
