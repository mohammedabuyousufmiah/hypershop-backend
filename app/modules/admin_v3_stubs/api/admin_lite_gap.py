"""admin-lite security console read endpoints for AdminLiteClient.

The admin-panel ``AdminLiteClient.tsx`` ("Security & access") calls three
GET paths under ``/api/v1/admin/admin-lite/*`` with raw ``apiFetch``
(cookie auth + envelope unwrap). Without these, the IP-allowlist, RBAC,
and Alerts tabs show "Not available yet" (their 404 fallback). This
module mounts those reads so each tab connects and renders — an empty
state when no data, real rows when a plausible backing table exists.

The security-hardening pack tables (ip_allowlist / rbac grants /
security alerts) do NOT ship as a dedicated schema in this build, so
every endpoint is fully defensive: it probes for a plausibly-named
backing table via raw ``text()`` SQL inside try/except and falls back
to the correct EMPTY shape. The file imports only stable core symbols,
so it is boot-safe on its own and never 500s on a missing table/column.

Wire shapes (what the FE's ``apiFetch`` reads as ``.data`` — each cast
as ``unknown[]`` by the client, so a RAW JSON LIST):

* ``GET /admin/admin-lite/ip-allowlist``            -> ``[ {scope,label,cidr,...} ]``
* ``GET /admin/admin-lite/rbac/grants/{role_code}`` -> ``[ {role_code,scope_code,conditions,...} ]``
* ``GET /admin/admin-lite/alerts``                  -> ``[ {id,severity,status,created_at} ]``

The alerts rows carry the columns the FE table reads (id / severity /
status / created_at). When a real table is found these are mapped
best-effort; otherwise the list is empty.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_session
from app.core.security.rbac import requires_permission

# Same gate the rest of the admin-v3 surface uses — every admin staffer
# already holds it, so the console renders for them and 403s for others.
_PERM = "catalog.product.write"
_GUARD = [Depends(requires_permission(_PERM))]

router = APIRouter(
    prefix="/admin/admin-lite", tags=["admin-admin-lite"], dependencies=_GUARD
)


async def _table_exists(session: AsyncSession, name: str) -> bool:
    """True when a regclass with ``name`` resolves. Never raises."""
    try:
        res = await session.execute(
            text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}
        )
        return res.scalar() is not None
    except Exception:
        return False


async def _safe_rows(
    session: AsyncSession, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run a read query, returning a list of plain dicts. Never raises."""
    try:
        res = await session.execute(text(sql), params or {})
        return [dict(m) for m in res.mappings().all()]
    except Exception:
        return []


async def _first_table(session: AsyncSession, candidates: list[str]) -> str | None:
    """Return the first candidate table name that exists, else None."""
    for name in candidates:
        if await _table_exists(session, name):
            return name
    return None


# ────────────────────────────────────────────────────────────────────────
# IP allowlist — CIDR entries the operator manages.
# FE reads: rendered as JSON when non-empty; EmptyState otherwise.
# Returns a RAW LIST.
# ────────────────────────────────────────────────────────────────────────
@router.get("/ip-allowlist")
async def list_ip_entries(
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    table = await _first_table(
        session,
        ["ip_allowlist_entries", "ip_allowlist", "admin_ip_allowlist"],
    )
    if table is None:
        return []
    # Select * so we tolerate whatever columns the table happens to have;
    # the FE just JSON-renders the rows.
    rows = await _safe_rows(
        session, f"SELECT * FROM {table} ORDER BY id DESC LIMIT :limit", {"limit": limit}
    )
    return rows


# ────────────────────────────────────────────────────────────────────────
# RBAC grants for a role — scope grants attached to a role_code.
# FE reads: rendered as JSON. Returns a RAW LIST.
# ────────────────────────────────────────────────────────────────────────
@router.get("/rbac/grants/{role_code}")
async def list_role_grants(
    role_code: str = Path(..., min_length=1, max_length=128),
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    table = await _first_table(
        session,
        ["rbac_role_grants", "rbac_grants", "role_scope_grants", "admin_role_grants"],
    )
    if table is None:
        return []
    rows = await _safe_rows(
        session,
        f"SELECT * FROM {table} WHERE role_code = :rc ORDER BY id DESC LIMIT :limit",
        {"rc": role_code, "limit": limit},
    )
    return rows


# ────────────────────────────────────────────────────────────────────────
# Alert inbox — security alerts the operator acks/snoozes/resolves.
# FE reads per row: id, severity, status, created_at. Returns a RAW LIST.
# Query: severity_min (str, optional), include_snoozed (bool).
# ────────────────────────────────────────────────────────────────────────
@router.get("/alerts")
async def list_alerts(
    severity_min: str | None = Query(None),
    include_snoozed: bool = Query(False),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    table = await _first_table(
        session,
        ["security_alerts", "admin_alerts", "admin_lite_alerts", "ops_alerts"],
    )
    if table is None:
        return []
    where: list[str] = []
    if not include_snoozed:
        # Best-effort: only filter if a status column is present; the
        # _safe_rows try/except swallows it otherwise and we re-query plain.
        where.append("status <> 'snoozed'")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = await _safe_rows(
        session,
        f"SELECT * FROM {table}{clause} ORDER BY id DESC LIMIT :limit",
        {"limit": limit},
    )
    if not rows:
        # Either empty, or the optional WHERE referenced a missing column;
        # retry unfiltered so a real (un-snoozed-column) table still renders.
        rows = await _safe_rows(
            session,
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT :limit",
            {"limit": limit},
        )
    if severity_min and rows:
        order = {"info": 0, "warning": 1, "high": 2, "critical": 3}
        floor = order.get(severity_min.lower())
        if floor is not None:
            rows = [
                r
                for r in rows
                if order.get(str(r.get("severity", "")).lower(), 0) >= floor
            ]
    return rows
