"""WMS (Noon-CFC warehouse console) read endpoints for AdminWmsClient.

The admin-panel ``AdminWmsClient.tsx`` calls five GET paths under
``/api/v1/admin/wms/*`` with raw ``apiFetch`` (cookie auth + envelope
unwrap). This module mounts those reads so the console connects and
renders instead of showing "Not available yet" (its 404 fallback).

No dedicated WMS schema (ASN / bins / shipments / NDR tables) ships in
this build, so every endpoint is fully defensive: it probes for a
plausibly-named backing table via raw ``text()`` SQL inside a
try/except and falls back to the correct EMPTY shape. The file imports
only stable core symbols, so it is boot-safe on its own and never 500s
on a missing table/column.

Wire shapes (what the FE's ``apiFetch`` reads as ``.data``):
* ``GET /admin/wms/dashboard``  -> the 8-key rollup object (``Dash``).
* ``GET /admin/wms/asn``        -> ``{"items": [...]}``
* ``GET /admin/wms/shipments``  -> ``{"items": [...]}``
* ``GET /admin/wms/ndr``        -> ``{"items": [...]}``
* ``GET /admin/wms/bins``       -> ``{"items": [...]}``

The list rows carry the columns the FE's DataTable reads (asn_no /
vendor_name / status / ...). When a real table is found these are
mapped best-effort; otherwise the list is empty.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_session
from app.core.security.rbac import requires_permission

# Same gate the rest of the admin-v3 surface uses — every admin staffer
# already holds it, so the console renders for them and 403s for others.
_PERM = "catalog.product.write"
_GUARD = [Depends(requires_permission(_PERM))]

router = APIRouter(prefix="/admin/wms", tags=["admin-wms"], dependencies=_GUARD)


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


async def _count(session: AsyncSession, table: str, where: str = "") -> int:
    """COUNT(*) for ``table`` (optionally filtered). 0 on any failure."""
    if not await _table_exists(session, table):
        return 0
    clause = f" WHERE {where}" if where else ""
    rows = await _safe_rows(session, f"SELECT COUNT(*) AS c FROM {table}{clause}")
    if rows:
        try:
            return int(rows[0].get("c") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


# ────────────────────────────────────────────────────────────────────────
# Dashboard rollup — 8 KPI counters the AdminWmsClient header renders.
# ────────────────────────────────────────────────────────────────────────
@router.get("/dashboard")
async def wms_dashboard(
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    # Best-effort counts off whatever WMS-ish tables happen to exist;
    # all default to 0 so the KPI cards render a clean empty state.
    asn_open = await _count(session, "wms_asn", "status NOT IN ('received','closed')")
    ndr_open = await _count(session, "wms_ndr", "action = 'pending'")
    bins = await _count(session, "wms_bins")
    shipments_in_flight = await _count(
        session, "wms_shipments", "status IN ('dispatched','manifested','awb_generated')"
    )
    # SKUs with stock — reuse the real inventory stock_balances table when present.
    skus_with_stock = await _count(session, "stock_balances", "on_hand > 0")

    return {
        "asn_open": asn_open,
        "receiving_pending": await _count(session, "wms_asn", "status = 'receiving'"),
        "putaway_pending": await _count(session, "wms_putaway", "status = 'pending'"),
        "pick_jobs_open": await _count(session, "wms_pick_jobs", "status = 'open'"),
        "shipments_in_flight": shipments_in_flight,
        "ndr_open": ndr_open,
        "bins": bins,
        "skus_with_stock": skus_with_stock,
    }


# ────────────────────────────────────────────────────────────────────────
# Inbound — Advance Shipment Notices.
# Cols read by FE: asn_no, vendor_name, status, expected_qty, received_qty (+id).
# ────────────────────────────────────────────────────────────────────────
@router.get("/asn")
async def wms_asn(
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    if not await _table_exists(session, "wms_asn"):
        return {"items": []}
    rows = await _safe_rows(
        session,
        """
        SELECT id, asn_no, vendor_name, status, expected_qty, received_qty
        FROM wms_asn
        ORDER BY id DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return {"items": rows}


# ────────────────────────────────────────────────────────────────────────
# Outbound — Shipments.
# Cols read by FE: shipment_no, courier, dest_city, weight_g, status (+id).
# ────────────────────────────────────────────────────────────────────────
@router.get("/shipments")
async def wms_shipments(
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    if not await _table_exists(session, "wms_shipments"):
        return {"items": []}
    rows = await _safe_rows(
        session,
        """
        SELECT id, shipment_no, courier, dest_city, weight_g, status
        FROM wms_shipments
        ORDER BY id DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return {"items": rows}


# ────────────────────────────────────────────────────────────────────────
# Exceptions — Non-Delivery Reports.
# Cols read by FE: reason_code, attempt_no, action (+id).
# ────────────────────────────────────────────────────────────────────────
@router.get("/ndr")
async def wms_ndr(
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    if not await _table_exists(session, "wms_ndr"):
        return {"items": []}
    rows = await _safe_rows(
        session,
        """
        SELECT id, reason_code, attempt_no, action
        FROM wms_ndr
        ORDER BY id DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return {"items": rows}


# ────────────────────────────────────────────────────────────────────────
# Storage bins.
# Cols read by FE: bin_code, zone, bin_type, capacity, is_active (+id).
# ────────────────────────────────────────────────────────────────────────
@router.get("/bins")
async def wms_bins(
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    if not await _table_exists(session, "wms_bins"):
        return {"items": []}
    rows = await _safe_rows(
        session,
        """
        SELECT id, bin_code, zone, bin_type, capacity, is_active
        FROM wms_bins
        ORDER BY bin_code ASC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return {"items": rows}


__all__ = ["router"]
