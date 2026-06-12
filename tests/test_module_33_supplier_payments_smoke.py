"""Integration smoke for Module 33 — Supplier Payment Approval Engine."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.db.session import get_engine

pytestmark = pytest.mark.integration


# ---------------- migration ----------------
async def test_migration_0027_created_tables() -> None:
    expected = {
        "supplier_bill_approval_state",
        "supplier_bill_approvals",
        "supplier_payment_recommendations",
        "supplier_bank_accounts",
        "supplier_payment_approval_workflows",
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


async def test_migration_0027_added_payment_verification_columns() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_name='fin_supplier_payments' "
                    "and column_name in "
                    "('verification_status','proof_file_url','executed_by',"
                    "'verified_by','verified_at','bank_account_id')",
                ),
            )
        ).all()
    cols = {r[0] for r in rows}
    assert cols == {
        "verification_status", "proof_file_url", "executed_by",
        "verified_by", "verified_at", "bank_account_id",
    }


async def test_approvals_table_is_append_only_for_public_role() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "select has_table_privilege('public',"
                "'supplier_bill_approvals','UPDATE')",
            ),
        )
        public_can_update = result.scalar_one()
    assert public_can_update is False


# ---------------- workflow seed ----------------
async def test_workflow_seed_creates_standard_and_high_value() -> None:
    """The lifespan hook should have seeded both workflows."""
    from app.modules.supplier_payments.workflow_seed import (
        seed_default_workflows,
    )
    counts = await seed_default_workflows()
    assert counts["workflows_seeded"] == 2

    engine = get_engine()
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select workflow_code, requires_super_admin, "
                    "min_approval_steps "
                    "from supplier_payment_approval_workflows",
                ),
            )
        ).all()
    by_code = {r[0]: (r[1], r[2]) for r in rows}
    assert "standard" in by_code
    assert "high_value" in by_code
    # standard: no super-admin, 3 steps
    assert by_code["standard"] == (False, 3)
    # high_value: super-admin required, 4 steps
    assert by_code["high_value"] == (True, 4)


# ---------------- routes wired ----------------
async def test_approval_queue_requires_finance_perm(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/admin/supplier-payments/queue")
    assert resp.status_code in (401, 403)


async def test_recommended_endpoint_requires_finance_perm(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get(
        "/api/v1/admin/supplier-payments/recommended",
    )
    assert resp.status_code in (401, 403)


async def test_workflows_list_requires_finance_perm(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get(
        "/api/v1/admin/supplier-payments/workflows",
    )
    assert resp.status_code in (401, 403)
