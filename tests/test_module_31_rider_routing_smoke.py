"""Integration smoke for Module 31 — Rider Routing.

Confirms migration 0025 created the 7 tables + ALTER on ``riders``,
and that all rider/admin endpoints are wired (auth-gated).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.db.session import get_engine

pytestmark = pytest.mark.integration


# ---------------- migration ----------------
async def test_migration_0025_created_tables() -> None:
    expected = {
        "rider_shifts",
        "rider_live_locations",
        "run_sheets",
        "run_sheet_stops",
        "route_recalculation_logs",
        "route_eta_snapshots",
        "ops_route_overrides",
    }
    engine = get_engine()
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select tablename from pg_tables "
                    "where schemaname='public' AND tablename = ANY(:names)",
                ),
                {"names": list(expected)},
            )
        ).all()
    found = {r[0] for r in rows}
    assert found == expected, f"missing: {expected - found}"


async def test_migration_0025_added_columns_to_riders() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_name='riders' "
                    "and column_name in "
                    "('capacity_orders','max_cash_limit_bdt','hub_code')",
                ),
            )
        ).all()
    cols = {r[0] for r in rows}
    assert cols == {"capacity_orders", "max_cash_limit_bdt", "hub_code"}


# ---------------- routes wired ----------------
async def test_admin_live_map_requires_dispatch_perm(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/admin/rider-dispatch/live-map")
    assert resp.status_code in (401, 403)


async def test_admin_run_sheets_requires_dispatch_perm(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/admin/rider-dispatch/run-sheets")
    assert resp.status_code in (401, 403)


async def test_rider_current_run_sheet_requires_auth(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/rider/run-sheet/current")
    assert resp.status_code in (401, 403)


async def test_rider_shift_start_requires_auth(
    api_client: AsyncClient,
) -> None:
    # Empty body → would be 422 if we got past auth; we expect 401/403.
    resp = await api_client.post("/api/v1/rider/shifts/start", json={})
    assert resp.status_code in (401, 403)
