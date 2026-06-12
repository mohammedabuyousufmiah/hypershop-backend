"""Mobile customer-app integration tests.

Coverage:
- Profile read + update (incl. phone change resets verification)
- Device token register (idempotent upsert), list, deactivate
- Saved addresses CRUD with one-default invariant
- Aggregated home payload includes all blocks
- Anonymous track endpoint: code + phone last-4 gates access
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.db.session import get_sessionmaker

pytestmark = pytest.mark.integration


# ============================================================
# Profile
# ============================================================


async def test_get_profile_returns_signed_in_user(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    resp = await api_client.get("/api/v1/me/profile", headers=logged_in["headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == logged_in["email"]
    assert body["full_name"]


async def test_update_profile_changes_name_and_phone(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    resp = await api_client.patch(
        "/api/v1/me/profile",
        headers=logged_in["headers"],
        json={"full_name": "Updated Name", "phone": "+8801911000123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["full_name"] == "Updated Name"
    assert body["phone"] == "+8801911000123"
    # Phone change resets verification timestamp.
    assert body["phone_verified_at"] is None


async def test_profile_requires_auth(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/me/profile")
    assert resp.status_code == 401


# ============================================================
# Device tokens
# ============================================================


async def test_register_device_creates_then_upserts(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    body = {"kind": "fcm", "token": "fcm-test-token-aaaa", "app_version": "1.0.0", "locale": "bn-BD"}
    r1 = await api_client.post(
        "/api/v1/me/devices", headers=logged_in["headers"], json=body,
    )
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["id"]

    # Same token re-posted by same user → upsert (returns same row id).
    r2 = await api_client.post(
        "/api/v1/me/devices", headers=logged_in["headers"],
        json={**body, "app_version": "1.0.1"},
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == first_id
    assert r2.json()["app_version"] == "1.0.1"

    listing = await api_client.get(
        "/api/v1/me/devices", headers=logged_in["headers"],
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1


async def test_deactivate_device_succeeds(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    r = await api_client.post(
        "/api/v1/me/devices", headers=logged_in["headers"],
        json={"kind": "apns", "token": "apns-aabbccdd"},
    )
    did = r.json()["id"]
    delete = await api_client.delete(
        f"/api/v1/me/devices/{did}", headers=logged_in["headers"],
    )
    assert delete.status_code == 204
    listing = await api_client.get(
        "/api/v1/me/devices", headers=logged_in["headers"],
    )
    # is_active=False filters it out of the list.
    assert listing.json() == []


async def test_device_kind_validation(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    r = await api_client.post(
        "/api/v1/me/devices", headers=logged_in["headers"],
        json={"kind": "carrier-pigeon", "token": "x" * 32},
    )
    assert r.status_code == 422


# ============================================================
# Addresses
# ============================================================


def _addr_body(label: str = "Home", default: bool = False) -> dict[str, Any]:
    return {
        "label": label,
        "recipient_name": "Test Customer",
        "phone": "+8801911000456",
        "line1": "House 7, Road 5",
        "city": "Dhaka",
        "district": "Dhaka",
        "division": "Dhaka",
        "country": "BD",
        "is_default": default,
    }


async def test_address_crud_and_default_invariant(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    h = logged_in["headers"]

    a1 = await api_client.post("/api/v1/me/addresses", headers=h, json=_addr_body("Home", True))
    assert a1.status_code == 201
    a1_id = a1.json()["id"]
    assert a1.json()["is_default"] is True

    # Adding a second default demotes the first.
    a2 = await api_client.post("/api/v1/me/addresses", headers=h, json=_addr_body("Office", True))
    assert a2.status_code == 201

    listing = await api_client.get("/api/v1/me/addresses", headers=h)
    items = listing.json()
    defaults = [x for x in items if x["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["label"] == "Office"

    # Patch the first one to default again — same demotion happens.
    p = await api_client.patch(
        f"/api/v1/me/addresses/{a1_id}", headers=h, json={"is_default": True},
    )
    assert p.status_code == 200
    listing2 = (await api_client.get("/api/v1/me/addresses", headers=h)).json()
    defaults2 = [x for x in listing2 if x["is_default"]]
    assert len(defaults2) == 1
    assert defaults2[0]["id"] == a1_id

    # Delete works.
    d = await api_client.delete(f"/api/v1/me/addresses/{a1_id}", headers=h)
    assert d.status_code == 204
    final = (await api_client.get("/api/v1/me/addresses", headers=h)).json()
    assert all(a["id"] != a1_id for a in final)


async def test_address_belongs_to_caller(
    api_client: AsyncClient, logged_in: dict[str, Any], admin_user: dict[str, Any],
) -> None:
    a = await api_client.post(
        "/api/v1/me/addresses", headers=logged_in["headers"], json=_addr_body(),
    )
    aid = a.json()["id"]
    # Admin trying to PATCH another user's address → 404 (not 403, to avoid leaking existence).
    bad = await api_client.patch(
        f"/api/v1/me/addresses/{aid}", headers=admin_user["headers"], json={"label": "X"},
    )
    assert bad.status_code == 404


# ============================================================
# Anonymous tracking
# ============================================================


async def test_track_order_requires_correct_phone_last4(
    api_client: AsyncClient,
) -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        oid = (
            await s.execute(
                text(
                    """INSERT INTO users (email, password_hash, status, full_name)
                       VALUES ('track@hypershop.dev', 'x', 'active', 'Track')
                       RETURNING id"""
                ),
            )
        ).scalar_one()
        await s.execute(
            text(
                """INSERT INTO orders (
                       code, customer_user_id, status, payment_method,
                       currency, subtotal, grand_total, delivery_address
                   )
                   VALUES ('HSO-TRACK1', :u, 'completed', 'cod',
                           'BDT', 200, 200,
                           '{"recipient_name":"X","phone":"+8801911007890","line1":"y","city":"Dhaka","country":"BD"}'::jsonb)
                """,
            ),
            {"u": oid},
        )

    # Wrong phone → 404 (don't disclose existence).
    bad = await api_client.get(
        "/api/v1/track/orders/HSO-TRACK1", params={"phone_last4": "0000"},
    )
    assert bad.status_code == 404

    # Correct phone → 200.
    ok = await api_client.get(
        "/api/v1/track/orders/HSO-TRACK1", params={"phone_last4": "7890"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["code"] == "HSO-TRACK1"
    assert body["status"] == "completed"

    # Bad phone format → 422 (Query validation).
    bad_fmt = await api_client.get(
        "/api/v1/track/orders/HSO-TRACK1", params={"phone_last4": "abcd"},
    )
    assert bad_fmt.status_code == 422


async def test_track_unknown_order_returns_404(api_client: AsyncClient) -> None:
    r = await api_client.get(
        "/api/v1/track/orders/HSO-NOPE", params={"phone_last4": "1234"},
    )
    assert r.status_code == 404


# ============================================================
# Aggregated home
# ============================================================


async def test_home_returns_all_blocks(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    # Seed a default address so the home payload exercises that branch.
    await api_client.post(
        "/api/v1/me/addresses", headers=logged_in["headers"],
        json=_addr_body("Home", True),
    )
    r = await api_client.get("/api/v1/mobile/home", headers=logged_in["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"]["email"] == logged_in["email"]
    assert body["default_address"]["label"] == "Home"
    assert body["recent_orders"] == []
    assert body["counters"] == {
        "active_orders": 0,
    }


