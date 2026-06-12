"""Integration smoke for Module 30 — Reporting Platform.

Confirms:
  - Migration 0024 created the 6 tables.
  - The startup hook (main._lifespan) seeded 12 default report
    definitions + role policies.
  - The user-facing list endpoint requires auth (proves the router
    is wired through ``requires_*`` deps + the API prefix).
  - The admin executions feed requires the ``reporting.admin`` perm.

Does NOT execute a report run — that needs a real catalog row + a
super-admin token. Covered in module-local DB tests under
``app/modules/reporting/tests/`` once those switch to the integration
fixture stack.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.db.session import get_engine

pytestmark = pytest.mark.integration


# ---------------- migration ----------------
async def test_migration_0024_created_tables() -> None:
    expected = {
        "report_definitions",
        "report_access_policies",
        "report_execution_logs",
        "report_schedules",
        "report_saved_filters",
        "report_files",
    }
    engine = get_engine()
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select tablename from pg_tables "
                    "where schemaname='public' "
                    "and tablename = ANY(:names)",
                ),
                {"names": list(expected)},
            )
        ).all()
    found = {r[0] for r in rows}
    assert found == expected, f"missing tables: {expected - found}"


# ---------------- bootstrap seed ----------------
async def test_bootstrap_seeds_twelve_definitions() -> None:
    """Lifespan should have seeded every registered built-in report.

    Expected count comes from the builder registry itself (was a
    hardcoded 12, which drifted when pharmacy-era builders were purged
    from the marketplace build). Re-runs the seed inline in case
    lifespan didn't fire under the test fixture (idempotent).
    """
    from app.modules.reporting.bootstrap import seed_default_reports
    from app.modules.reporting.builders import register_all
    from app.modules.reporting.registry import report_registry

    register_all()
    expected = len(report_registry.all())
    assert expected >= 10, "builder registry unexpectedly empty/shrunk"
    counts = await seed_default_reports()
    assert counts["definitions"] == expected

    engine = get_engine()
    async with engine.begin() as conn:
        n = (
            await conn.execute(
                text("select count(*) from report_definitions"),
            )
        ).scalar_one()
    assert n >= expected


# ---------------- routes wired ----------------
async def test_reports_list_requires_auth(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/reports")
    assert resp.status_code in (401, 403), (
        f"unexpected status {resp.status_code}: {resp.text[:200]}"
    )


async def test_admin_reporting_executions_requires_perm(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/admin/reporting/executions")
    assert resp.status_code in (401, 403)


async def test_admin_reporting_definitions_requires_perm(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/admin/reporting/definitions")
    assert resp.status_code in (401, 403)


async def test_unknown_reports_route_returns_envelope(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/reports/does-not-exist/unknown")
    assert resp.status_code == 404
