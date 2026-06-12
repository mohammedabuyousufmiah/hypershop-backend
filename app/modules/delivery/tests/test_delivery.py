from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


# ---------------- Quote (public) ----------------


async def test_quote_for_dhaka_metro_returns_50(api_client: AsyncClient) -> None:
    """Service-area zone seeded by migration → 50 BDT flat."""
    resp = await api_client.post(
        "/api/v1/delivery/quote",
        json={"address": {"city": "Dhaka"}, "payment_method": "cod"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["zone_code"] == "DHAKA-METRO"
    assert body["kind"] == "service_area"
    assert body["base_fee"] == "50.00"
    assert body["cod_fee"] == "0.00"
    assert body["total"] == "50.00"
    assert body["currency"] == "BDT"


async def test_quote_for_3pl_zone_returns_band_price(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/delivery/quote",
        json={"address": {"city": "Savar"}, "payment_method": "online"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["zone_code"] == "DHAKA-OUTER"
    assert body["kind"] == "3pl"
    assert body["base_fee"] == "100.00"
    # The 3PL band: 70 ≤ price ≤ 150
    from decimal import Decimal
    assert Decimal("70") <= Decimal(body["base_fee"]) <= Decimal("150")


async def test_quote_cod_charge_is_zero_regardless_of_zone(
    api_client: AsyncClient,
) -> None:
    for city in ("Dhaka", "Savar"):
        resp = await api_client.post(
            "/api/v1/delivery/quote",
            json={"address": {"city": city}, "payment_method": "cod"},
        )
        assert resp.status_code == 200
        assert resp.json()["cod_fee"] == "0.00", city


async def test_quote_falls_back_to_default_zone(api_client: AsyncClient) -> None:
    """Unknown city → falls back to the seeded default zone (Dhaka Metro)."""
    resp = await api_client.post(
        "/api/v1/delivery/quote",
        json={"address": {"city": "Atlantis"}, "payment_method": "online"},
    )
    assert resp.status_code == 200
    # Default seed = DHAKA-METRO.
    assert resp.json()["zone_code"] == "DHAKA-METRO"


async def test_quote_postal_code_overrides_city(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    """Add a zone keyed on postal_code → that wins over the seeded city match."""
    create = await api_client.post(
        "/api/v1/admin/delivery/zones",
        headers=admin_user["headers"],
        json={
            "code": "DHAKA-1212-VIP",
            "name": "Banani VIP route",
            "kind": "service_area",
            "price": "50.00",
            "cities": [],
            "postal_codes": ["1212"],
            "is_default": False,
        },
    )
    assert create.status_code == 201, create.text

    resp = await api_client.post(
        "/api/v1/delivery/quote",
        json={
            "address": {"city": "Dhaka", "postal_code": "1212"},
            "payment_method": "cod",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["zone_code"] == "DHAKA-1212-VIP"


# ---------------- Listing ----------------


async def test_public_zones_lists_active_only(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    # Deactivate one of the seeded zones.
    seeded = await api_client.get(
        "/api/v1/admin/delivery/zones", headers=admin_user["headers"],
    )
    by_code = {z["code"]: z for z in seeded.json()}
    target = by_code["OUTSIDE-DHAKA"]
    deactivate = await api_client.patch(
        f"/api/v1/admin/delivery/zones/{target['id']}",
        headers=admin_user["headers"],
        json={"is_active": False},
    )
    assert deactivate.status_code == 200

    public = await api_client.get("/api/v1/delivery/zones")
    assert public.status_code == 200
    codes = {z["code"] for z in public.json()}
    assert "OUTSIDE-DHAKA" not in codes
    assert "DHAKA-METRO" in codes


# ---------------- Validation: rule enforcement ----------------


async def test_service_area_must_be_50(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/delivery/zones",
        headers=admin_user["headers"],
        json={
            "code": "BAD-SVC",
            "name": "Bad",
            "kind": "service_area",
            "price": "60.00",
        },
    )
    assert resp.status_code == 422


async def test_3pl_must_be_in_band(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    too_low = await api_client.post(
        "/api/v1/admin/delivery/zones",
        headers=admin_user["headers"],
        json={"code": "LOW", "name": "Low", "kind": "3pl", "price": "60.00"},
    )
    assert too_low.status_code == 422

    too_high = await api_client.post(
        "/api/v1/admin/delivery/zones",
        headers=admin_user["headers"],
        json={"code": "HI", "name": "Hi", "kind": "3pl", "price": "200.00"},
    )
    assert too_high.status_code == 422


async def test_3pl_at_band_edges_accepted(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    low_edge = await api_client.post(
        "/api/v1/admin/delivery/zones",
        headers=admin_user["headers"],
        json={"code": "EDGE-LO", "name": "Lo edge", "kind": "3pl", "price": "70.00"},
    )
    assert low_edge.status_code == 201
    hi_edge = await api_client.post(
        "/api/v1/admin/delivery/zones",
        headers=admin_user["headers"],
        json={"code": "EDGE-HI", "name": "Hi edge", "kind": "3pl", "price": "150.00"},
    )
    assert hi_edge.status_code == 201


# ---------------- Default-zone uniqueness ----------------


async def test_setting_new_default_unsets_previous(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """Promoting a non-default zone to default should demote the old default
    in the same transaction, so the partial-unique index never violates.
    """
    listing = await api_client.get(
        "/api/v1/admin/delivery/zones", headers=admin_user["headers"],
    )
    by_code = {z["code"]: z for z in listing.json()}
    not_default = by_code["DHAKA-OUTER"]
    assert not_default["is_default"] is False
    assert by_code["DHAKA-METRO"]["is_default"] is True

    promote = await api_client.patch(
        f"/api/v1/admin/delivery/zones/{not_default['id']}",
        headers=admin_user["headers"],
        json={"is_default": True},
    )
    assert promote.status_code == 200, promote.text
    assert promote.json()["is_default"] is True

    # The old default is now demoted.
    after = await api_client.get(
        "/api/v1/admin/delivery/zones", headers=admin_user["headers"],
    )
    new_by_code = {z["code"]: z for z in after.json()}
    assert new_by_code["DHAKA-METRO"]["is_default"] is False
    assert new_by_code["DHAKA-OUTER"]["is_default"] is True


# ---------------- Audit ----------------


async def test_zone_create_writes_audit(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    from sqlalchemy import select

    from app.core.audit.models import AuditLog
    from app.core.db.session import get_sessionmaker

    create = await api_client.post(
        "/api/v1/admin/delivery/zones",
        headers=admin_user["headers"],
        json={
            "code": "AUDIT-Z",
            "name": "Audit Watch",
            "kind": "3pl",
            "price": "120.00",
        },
    )
    assert create.status_code == 201
    zone_id = create.json()["id"]

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "delivery.zone.create",
                        AuditLog.resource_id == zone_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


# ---------------- RBAC ----------------


async def test_customer_cannot_create_zone(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/delivery/zones",
        headers=logged_in["headers"],
        json={"code": "X", "name": "X", "kind": "service_area", "price": "50.00"},
    )
    assert resp.status_code == 403


async def test_anon_cannot_create_zone(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/admin/delivery/zones",
        json={"code": "X", "name": "X", "kind": "service_area", "price": "50.00"},
    )
    assert resp.status_code == 401


async def test_quote_is_public(api_client: AsyncClient) -> None:
    """No auth required for delivery quotes."""
    resp = await api_client.post(
        "/api/v1/delivery/quote",
        json={"address": {"city": "Dhaka"}, "payment_method": "cod"},
    )
    assert resp.status_code == 200
