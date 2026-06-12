"""WMS (Noon-CFC warehouse console) ACTION endpoints for AdminWmsClient.

Companion to ``wms_gap.py`` (the read surface). The admin-panel
``AdminWmsClient.tsx`` fires five POST mutations on its own rows:

* ``POST /admin/wms/asn/{id}/gate-in``         body ``{doc_type, gate_in_qty}``
* ``POST /admin/wms/asn/{id}/start-receiving``
* ``POST /admin/wms/asn/{id}/grn``             (close GRN -> received)
* ``POST /admin/wms/shipments/{id}/delivered``
* ``POST /admin/wms/ndr/{id}/resolve``         body ``{action: reattempt|rto}``

Every handler runs raw ``text()`` SQL against the SAME real tables the
GET gap router reads (``wms_asn`` / ``wms_shipments`` / ``wms_ndr``) so a
mutation here is immediately visible on the next dashboard/list refresh.

Fully defensive, mirroring the read file: if the table or a column is
absent the handler returns a clean ``200 {"ok": false,
"reason": "not_available"}`` instead of raising — it never 500s. Each
write is wrapped in an explicit transaction (commit on success,
rollback on failure). Imports only stable core symbols, so it is
boot-safe on its own.

Same RBAC gate as the read surface (``catalog.product.write``), applied
at the router level so it inherits identically.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_session
from app.core.security.rbac import requires_permission

# Identical gate to wms_gap.py so the same staffers who can read can act.
_PERM = "catalog.product.write"
_GUARD = [Depends(requires_permission(_PERM))]

# Same prefix as the GET gap router -> the action paths line up 1:1 with
# what the FE builds (``/admin/wms/asn/{id}/...`` etc).
router = APIRouter(
    prefix="/admin/wms", tags=["admin-wms-actions"], dependencies=_GUARD
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


async def _apply(
    session: AsyncSession, sql: str, params: dict[str, Any]
) -> dict[str, Any] | None:
    """Run an UPDATE ... RETURNING in its own txn.

    Returns the updated row as a dict, ``None`` when no row matched, or
    raises only to be swallowed by the caller's not_available fallback.
    """
    res = await session.execute(text(sql), params)
    rows = res.mappings().all()
    await session.commit()
    return dict(rows[0]) if rows else None


async def _guarded_update(
    session: AsyncSession, table: str, sql: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Shared wrapper: existence check -> txn UPDATE -> defensive fallback."""
    if not await _table_exists(session, table):
        return _NOT_AVAILABLE
    try:
        row = await _apply(session, sql, params)
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        # Most likely a missing column on a differently-shaped table.
        return _NOT_AVAILABLE
    if row is None:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "row": row}


# ────────────────────────────────────────────────────────────────────────
# Inbound — ASN lifecycle: scheduled -> gated_in -> receiving -> received.
# ────────────────────────────────────────────────────────────────────────
@router.post("/asn/{asn_id}/gate-in")
async def asn_gate_in(
    asn_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # FE sends {doc_type, gate_in_qty}; we record the gate-in transition.
    return await _guarded_update(
        session,
        "wms_asn",
        """
        UPDATE wms_asn
           SET status = 'gated_in'
         WHERE id = :id AND status = 'scheduled'
        RETURNING id, asn_no, vendor_name, status, expected_qty, received_qty
        """,
        {"id": asn_id},
    )


@router.post("/asn/{asn_id}/start-receiving")
async def asn_start_receiving(
    asn_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _guarded_update(
        session,
        "wms_asn",
        """
        UPDATE wms_asn
           SET status = 'receiving'
         WHERE id = :id AND status IN ('gated_in', 'scheduled')
        RETURNING id, asn_no, vendor_name, status, expected_qty, received_qty
        """,
        {"id": asn_id},
    )


@router.post("/asn/{asn_id}/grn")
async def asn_close_grn(
    asn_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # Close the GRN: mark received and default received_qty to expected_qty
    # when it has not been counted in yet.
    return await _guarded_update(
        session,
        "wms_asn",
        """
        UPDATE wms_asn
           SET status = 'received',
               received_qty = COALESCE(NULLIF(received_qty, 0), expected_qty)
         WHERE id = :id AND status = 'receiving'
        RETURNING id, asn_no, vendor_name, status, expected_qty, received_qty
        """,
        {"id": asn_id},
    )


# ────────────────────────────────────────────────────────────────────────
# Outbound — Shipment delivery confirmation.
# ────────────────────────────────────────────────────────────────────────
@router.post("/shipments/{shipment_id}/delivered")
async def shipment_delivered(
    shipment_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _guarded_update(
        session,
        "wms_shipments",
        """
        UPDATE wms_shipments
           SET status = 'delivered'
         WHERE id = :id AND status = 'dispatched'
        RETURNING id, shipment_no, courier, dest_city, weight_g, status
        """,
        {"id": shipment_id},
    )


# ────────────────────────────────────────────────────────────────────────
# Exceptions — NDR resolution (reattempt | rto).
# ────────────────────────────────────────────────────────────────────────
@router.post("/ndr/{ndr_id}/resolve")
async def ndr_resolve(
    ndr_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # FE sends {action: "reattempt"} or {action: "rto"}.
    requested = str(body.get("action") or "").strip().lower()
    if requested not in {"reattempt", "rto"}:
        return {"ok": False, "reason": "invalid_action"}
    # A reattempt bumps the attempt counter; RTO closes the exception out.
    if requested == "reattempt":
        sql = """
            UPDATE wms_ndr
               SET action = 'reattempt',
                   attempt_no = COALESCE(attempt_no, 0) + 1
             WHERE id = :id AND action = 'pending'
            RETURNING id, reason_code, attempt_no, action
            """
    else:
        sql = """
            UPDATE wms_ndr
               SET action = 'rto'
             WHERE id = :id AND action = 'pending'
            RETURNING id, reason_code, attempt_no, action
            """
    return await _guarded_update(session, "wms_ndr", sql, {"id": ndr_id})


__all__ = ["router"]
