"""Integration test for the settlement reconciliation endpoint.

Covers the happy path with all three match outcomes (matched / drift /
orphan) in one request, plus the idempotency guard that rejects a
re-upload of the same (provider, business_date).

Uses a local admin fixture because the repo-root ``admin_user`` seeds
``root@hypershop.local`` and the current pydantic email-validator
rejects ``.local`` as a reserved TLD on login. Our fixture uses
``root@hypershop-recon-test.com`` which validates clean.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text as sa_text

from app.core.db.session import get_sessionmaker
from app.modules.orders.models import Order
from app.modules.payments.models import (
    PaymentIntent,
    SettlementMatch,
    SettlementReport,
)


@pytest.fixture
async def admin(api_client: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    """Seed an admin user with a TLD that passes the email validator,
    then log in to get an access token."""
    from app.core.security.passwords import hash_password
    from app.core.time import utc_now
    from app.modules.iam.models import User, UserStatus

    email = f"recon-admin-{uuid4().hex[:8]}@hypershop-recon-test.com"
    password = "AdminP@ssw0rdLong!"  # noqa: S105
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        user = User(
            email=email,
            full_name="Recon Admin",
            password_hash=hash_password(password),
            status=UserStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        s.add(user)
        await s.flush()
        admin_role_id = (
            await s.execute(sa_text("SELECT id FROM roles WHERE name = 'admin'"))
        ).scalar_one()
        await s.execute(
            sa_text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"
            ),
            {"u": user.id, "r": admin_role_id},
        )
    login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    # Response wrapped by the Standard Response Envelope middleware
    # ({success,message,data,meta}); login payload lives under .data.
    body = login.json()
    data = body.get("data", body)  # tolerate both wrapped + bare shapes
    yield {
        "email": email,
        "user_id": data["user"]["id"],
        "headers": {
            "Authorization": f"Bearer {data['tokens']['access_token']}",
        },
    }


@pytest.fixture
async def plain_customer(api_client: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    """A customer-role user used to verify the RBAC gate returns 403."""
    email = f"recon-customer-{uuid4().hex[:8]}@hypershop-recon-test.com"
    password = "CustomerP@ssw0rdLong!"  # noqa: S105
    reg = await api_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Cust"},
    )
    assert reg.status_code in (200, 201), reg.text
    login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    data = body.get("data", body)
    yield {
        "email": email,
        "headers": {
            "Authorization": f"Bearer {data['tokens']['access_token']}",
        },
    }


async def _seed_intents(customer_user_id: str) -> dict[str, Any]:
    """Seed two captured bKash intents the test will reconcile against.

    Returns a dict with the provider_payment_id strings + the matching
    ``amount_captured`` values, so the test body can craft the request
    body without copy-pasting magic numbers.
    """
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        order = Order(
            code=f"TST-{uuid4().hex[:8]}",
            customer_user_id=customer_user_id,
            status="payment_confirmed",
            payment_method="online",
            requires_prescription=False,
            currency="BDT",
            subtotal=Decimal("3000.00"),
            grand_total=Decimal("3000.00"),
            delivery_address={"city": "Dhaka"},
            placed_at=None,  # server default = now
        )
        s.add(order)
        await s.flush()

        intent_a = PaymentIntent(
            order_id=order.id,
            customer_user_id=customer_user_id,
            provider="bkash",
            provider_payment_id="BKASH-MATCH-001",
            status="captured",
            currency="BDT",
            amount=Decimal("1000.00"),
            amount_captured=Decimal("1000.00"),
        )
        intent_b = PaymentIntent(
            order_id=order.id,
            customer_user_id=customer_user_id,
            provider="bkash",
            provider_payment_id="BKASH-DRIFT-002",
            status="captured",
            currency="BDT",
            amount=Decimal("2000.00"),
            amount_captured=Decimal("2000.00"),
        )
        s.add_all([intent_a, intent_b])

    return {
        "match_ref": "BKASH-MATCH-001",   # report amount == captured → matched
        "drift_ref": "BKASH-DRIFT-002",   # report 2050 vs captured 2000 → drift 50
        "orphan_ref": "BKASH-UNKNOWN-999",  # not in DB → orphan
    }


@pytest.mark.asyncio
async def test_reconcile_matched_drift_orphan(
    api_client: AsyncClient, admin: dict[str, Any],
) -> None:
    refs = await _seed_intents(admin["user_id"])

    payload = {
        "provider": "bkash",
        "report_date": "2026-05-15",
        "currency": "BDT",
        "lines": [
            {"provider_ref": refs["match_ref"], "amount": "1000.00"},
            {"provider_ref": refs["drift_ref"], "amount": "2050.00"},
            {"provider_ref": refs["orphan_ref"], "amount": "777.00"},
        ],
    }

    resp = await api_client.post(
        "/api/v1/admin/payments/reconcile",
        json=payload,
        headers=admin["headers"],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Response envelope: payload sits under .data.
    body = body.get("data", body)

    report = body["report"]
    assert report["provider"] == "bkash"
    assert report["report_date"] == "2026-05-15"
    assert report["currency"] == "BDT"
    assert report["submitted_count"] == 3
    assert Decimal(report["submitted_total"]) == Decimal("3827.00")
    assert report["status"] == "processed"
    assert report["matched_count"] == 1
    assert report["drift_count"] == 1
    assert report["orphan_count"] == 1
    # drift_total = abs(2050 - 2000) = 50; orphan contributes 0
    assert Decimal(report["drift_total"]) == Decimal("50.00")

    matches_by_ref = {m["provider_ref"]: m for m in body["matches"]}
    assert matches_by_ref[refs["match_ref"]]["status"] == "matched"
    assert matches_by_ref[refs["drift_ref"]]["status"] == "drift"
    assert (
        Decimal(matches_by_ref[refs["drift_ref"]]["drift_amount"])
        == Decimal("50.00")
    )
    orphan_match = matches_by_ref[refs["orphan_ref"]]
    assert orphan_match["status"] == "orphan"
    assert orphan_match["payment_intent_id"] is None
    assert orphan_match["intent_amount"] is None

    # DB side: report + 3 match rows persisted, drift summary stored.
    sm = get_sessionmaker()
    async with sm() as s:
        row = (
            await s.execute(
                select(SettlementReport).where(
                    SettlementReport.id == report["id"],
                )
            )
        ).scalar_one()
        assert row.status == "processed"
        assert row.processed_at is not None
        assert row.processed_by is not None
        match_rows = (
            await s.execute(
                select(SettlementMatch).where(
                    SettlementMatch.settlement_report_id == row.id,
                )
            )
        ).scalars().all()
        assert len(match_rows) == 3


@pytest.mark.asyncio
async def test_reconcile_idempotency_rejects_duplicate_business_day(
    api_client: AsyncClient, admin: dict[str, Any],
) -> None:
    """Second submission for the same (provider, report_date) must fail."""
    await _seed_intents(admin["user_id"])
    payload = {
        "provider": "bkash",
        "report_date": "2026-05-14",
        "currency": "BDT",
        "lines": [{"provider_ref": "BKASH-MATCH-001", "amount": "1000.00"}],
    }
    first = await api_client.post(
        "/api/v1/admin/payments/reconcile",
        json=payload,
        headers=admin["headers"],
    )
    assert first.status_code == 200, first.text

    second = await api_client.post(
        "/api/v1/admin/payments/reconcile",
        json=payload,
        headers=admin["headers"],
    )
    # Service raises BusinessRuleError on the unique-constraint violation;
    # the global handler maps that to 4xx.
    assert second.status_code in (400, 409, 422), (
        f"Expected duplicate-day to be rejected; got {second.status_code}: {second.text}"
    )


@pytest.mark.asyncio
async def test_reconcile_requires_payments_reconcile_perm(
    api_client: AsyncClient, plain_customer: dict[str, Any],
) -> None:
    """A plain customer (no payments.reconcile perm) is 403'd at the gate."""
    resp = await api_client.post(
        "/api/v1/admin/payments/reconcile",
        json={
            "provider": "bkash",
            "report_date": "2026-05-13",
            "currency": "BDT",
            "lines": [{"provider_ref": "X", "amount": "1.00"}],
        },
        headers=plain_customer["headers"],
    )
    assert resp.status_code == 403, resp.text
