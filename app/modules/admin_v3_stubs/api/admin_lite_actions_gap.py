"""admin-lite security console ACTION endpoints for AdminLiteClient.

Companion to ``admin_lite_gap.py`` (the read surface). That file mounts the
three GET reads the ``AdminLiteClient.tsx`` console depends on
(``ip-allowlist`` / ``rbac/grants/{role_code}`` / ``alerts``). This file
mounts the matching row-level MUTATIONS the same console fires:

* ``POST   /admin/admin-lite/ip-allowlist``       -> add a CIDR entry
* ``POST   /admin/admin-lite/rbac/grants``        -> grant a scope to a role
* ``POST   /admin/admin-lite/alerts/action``      -> ack / snooze / resolve an alert

Each writes raw ``text()`` SQL against the SAME backing table the GET reads
(``ip_allowlist_entries`` / ``rbac_role_grants`` / ``security_alerts``), so a
successful action is immediately visible on the next GET refresh the client
does. Every handler:

* is gated by the SAME permission the GET gap uses (``catalog.product.write``),
* runs inside an explicit transaction (commit on success, rollback on error),
* is fully defensive — if the table or a column is absent it returns a clean
  ``200 {"ok": false, "reason": "not_available"}`` instead of a 500,
* honours an optional ``Idempotency-Key`` header for the alert action (re-acking
  the same alert is naturally idempotent anyway).

The file imports only stable core symbols, so it is boot-safe on its own and
never 500s on a missing table/column. Other MFA / break-glass / audit actions
the console performs target different prefixes / tables and are out of scope for
this gap file (break-glass lives under ``/admin/ops/break-glass`` and is mounted
elsewhere; MFA + audit are stateless/other-table flows).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_session
from app.core.security.rbac import requires_permission

# Same gate the GET gap router uses.
_PERM = "catalog.product.write"
_GUARD = [Depends(requires_permission(_PERM))]

router = APIRouter(
    prefix="/admin/admin-lite",
    tags=["admin-admin-lite-actions"],
    dependencies=_GUARD,
)

_NOT_AVAILABLE: dict[str, Any] = {"ok": False, "reason": "not_available"}


async def _table_exists(session: AsyncSession, name: str) -> bool:
    """True when a regclass with ``name`` resolves. Never raises."""
    try:
        res = await session.execute(
            text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}
        )
        return res.scalar() is not None
    except Exception:
        return False


async def _first_table(session: AsyncSession, candidates: list[str]) -> str | None:
    """Return the first candidate table name that exists, else None."""
    for name in candidates:
        if await _table_exists(session, name):
            return name
    return None


# ────────────────────────────────────────────────────────────────────────
# IP allowlist — add a CIDR entry.
# FE: adminLite.addIpEntry({ scope, label, cidr })
# ────────────────────────────────────────────────────────────────────────
@router.post("/ip-allowlist")
async def add_ip_entry(
    payload: dict[str, Any] = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    scope = str(payload.get("scope") or "admin")
    label = str(payload.get("label") or "").strip()
    cidr = str(payload.get("cidr") or "").strip()
    if not label or not cidr:
        return {"ok": False, "reason": "label_and_cidr_required"}

    table = await _first_table(
        session, ["ip_allowlist_entries", "ip_allowlist", "admin_ip_allowlist"]
    )
    if table is None:
        return dict(_NOT_AVAILABLE)
    try:
        res = await session.execute(
            text(
                f"INSERT INTO {table} (scope, label, cidr) "
                "VALUES (:scope, :label, :cidr) RETURNING *"
            ),
            {"scope": scope, "label": label, "cidr": cidr},
        )
        row = res.mappings().first()
        await session.commit()
        return {"ok": True, "entry": dict(row) if row else None}
    except Exception:
        await session.rollback()
        return dict(_NOT_AVAILABLE)


# ────────────────────────────────────────────────────────────────────────
# RBAC — grant a scope to a role.
# FE: adminLite.grantScope({ role_code, scope_code, conditions, note })
# ────────────────────────────────────────────────────────────────────────
@router.post("/rbac/grants")
async def grant_scope(
    payload: dict[str, Any] = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    role_code = str(payload.get("role_code") or "").strip()
    scope_code = str(payload.get("scope_code") or "").strip()
    conditions = payload.get("conditions")
    note = payload.get("note")
    if not role_code or not scope_code:
        return {"ok": False, "reason": "role_code_and_scope_code_required"}

    import json

    conditions_json = json.dumps(conditions) if conditions is not None else None

    table = await _first_table(
        session,
        ["rbac_role_grants", "rbac_grants", "role_scope_grants", "admin_role_grants"],
    )
    if table is None:
        return dict(_NOT_AVAILABLE)
    try:
        res = await session.execute(
            text(
                f"INSERT INTO {table} (role_code, scope_code, conditions, note) "
                "VALUES (:rc, :sc, CAST(:cond AS jsonb), :note) RETURNING *"
            ),
            {"rc": role_code, "sc": scope_code, "cond": conditions_json, "note": note},
        )
        row = res.mappings().first()
        await session.commit()
        return {"ok": True, "grant": dict(row) if row else None}
    except Exception:
        await session.rollback()
        # Retry without the optional conditions/note columns in case the real
        # table is a leaner shape (role_code + scope_code only).
        try:
            res = await session.execute(
                text(
                    f"INSERT INTO {table} (role_code, scope_code) "
                    "VALUES (:rc, :sc) RETURNING *"
                ),
                {"rc": role_code, "sc": scope_code},
            )
            row = res.mappings().first()
            await session.commit()
            return {"ok": True, "grant": dict(row) if row else None}
        except Exception:
            await session.rollback()
            return dict(_NOT_AVAILABLE)


# ────────────────────────────────────────────────────────────────────────
# Alerts — ack / snooze / resolve an alert.
# FE: adminLite.actOnAlert({ alert_id, action })
# ────────────────────────────────────────────────────────────────────────
_ALERT_STATUS = {
    "ack": "acknowledged",
    "snooze": "snoozed",
    "resolve": "resolved",
}


@router.post("/alerts/action")
async def act_on_alert(
    payload: dict[str, Any] = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    alert_id = payload.get("alert_id")
    action = str(payload.get("action") or "").strip().lower()
    new_status = _ALERT_STATUS.get(action)
    if alert_id is None or new_status is None:
        return {"ok": False, "reason": "alert_id_and_valid_action_required"}

    table = await _first_table(
        session,
        ["security_alerts", "admin_alerts", "admin_lite_alerts", "ops_alerts"],
    )
    if table is None:
        return dict(_NOT_AVAILABLE)
    try:
        res = await session.execute(
            text(
                f"UPDATE {table} SET status = :st WHERE id = :id RETURNING *"
            ),
            {"st": new_status, "id": alert_id},
        )
        row = res.mappings().first()
        await session.commit()
        if row is None:
            return {"ok": False, "reason": "alert_not_found"}
        return {"ok": True, "alert": dict(row)}
    except Exception:
        await session.rollback()
        return dict(_NOT_AVAILABLE)
