"""Rider mobile-app endpoint tests (Module 18).

Covers the new endpoints layered on top of the existing
``/rider/me/deliveries/*`` surface:

- ``POST /availability``
- ``GET  /tasks`` + ``GET /tasks/next``
- ``POST /{id}/scan``
- ``POST /{id}/upload-signature``
- ``GET  /cod-summary``

Reuses the same admin → onboard → place → dispatch → assign flow
established in test_deliveries.py.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.db.session import get_sessionmaker

pytestmark = pytest.mark.integration


_THREE_IMAGES = [
    {"url": f"https://cdn.hypershop.local/x/{i}.jpg", "kind": "image", "position": i}
    for i in range(3)
]


# ---------------- helpers ----------------


async def _create_variant(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    payload: dict[str, Any] = {
        "name": name, "base_currency": "BDT", "status": "active",
        "variants": [{"options": {}, "price": "100.00"}],
        "media": _THREE_IMAGES,
    }
    r = await client.post("/api/v1/admin/catalog/products", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["variants"][0]["id"]


async def _create_supplier(client: AsyncClient, headers: dict[str, str], code: str) -> str:
    r = await client.post(
        "/api/v1/admin/inventory/suppliers", headers=headers,
        json={"code": code, "name": f"Supplier {code}"},
    )
    assert r.status_code == 201
    return r.json()["id"]


async def _receive(
    client: AsyncClient, headers: dict[str, str], *,
    supplier_id: str, variant_id: str, invoice_number: str, quantity: int = 10,
) -> None:
    from datetime import date, timedelta
    body = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": invoice_number,
        "supplier_invoice_date": date.today().isoformat(),
        "warehouse_code": "MAIN", "currency": "BDT",
        "lines": [{
            "variant_id": variant_id, "quantity": quantity,
            "unit_cost": "10.00",
            "batch_number": f"B-{invoice_number}",
            "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
        }],
    }
    r = await client.post(
        "/api/v1/admin/inventory/goods-receipts", headers=headers, json=body,
    )
    assert r.status_code == 201, r.text


async def _onboard_rider(user_id: str, code: str) -> str:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        rid = (
            await s.execute(
                text(
                    """INSERT INTO riders
                         (code, name, phone, vehicle_type, linked_user_id, is_active)
                       VALUES (:c, 'Rider', '+8801911000001', 'motorcycle', :u, true)
                       RETURNING id"""
                ),
                {"c": code, "u": user_id},
            )
        ).scalar_one()
    return str(rid)


async def _drain_outbox() -> None:
    from app.core.events.dispatcher import dispatch_once
    for _ in range(20):
        n = await dispatch_once()
        if n == 0:
            break


async def _setup_assigned_delivery(
    api_client: AsyncClient, admin_user: dict, customer: dict, rider_user: dict,
) -> dict[str, str]:
    """Place + dispatch + assign one COD order. Returns dict with
    {order_id, order_code, assignment_id, rider_id}.
    """
    variant_id = await _create_variant(api_client, admin_user["headers"], "Item-RA")
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S-RA")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-RA-1", quantity=20,
    )
    place = await api_client.post(
        "/api/v1/orders", headers=customer["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 2}],
            "payment_method": "cod",
            "delivery_address": {
                "recipient_name": "Recipient X",
                "phone": "+8801911000777",
                "line1": "House 7",
                "city": "Dhaka",
                "country": "BD",
            },
            "currency": "BDT",
        },
    )
    assert place.status_code == 201, place.text
    order = place.json()
    await _drain_outbox()
    await api_client.post(
        f"/api/v1/admin/orders/{order['id']}/start-packing",
        headers=admin_user["headers"], json={},
    )
    await api_client.post(
        f"/api/v1/admin/orders/{order['id']}/dispatch",
        headers=admin_user["headers"], json={},
    )
    rider_id = await _onboard_rider(rider_user["user_id"], "R-MOB")
    assign = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order["id"], "rider_id": rider_id},
    )
    assert assign.status_code == 201, assign.text
    return {
        "order_id": order["id"],
        "order_code": order["code"],
        "assignment_id": assign.json()["id"],
        "rider_id": rider_id,
    }


# ============================================================
# Availability
# ============================================================


async def test_availability_toggle(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    await _onboard_rider(logged_in["user_id"], "R-AVL")
    r = await api_client.post(
        "/api/v1/rider/me/deliveries/availability",
        headers=logged_in["headers"], json={"status": "available"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "available"


async def test_cannot_go_offline_with_active_assignment(
    api_client: AsyncClient, admin_user: dict, logged_in: dict, registered_user: dict,
) -> None:
    # logged_in is the rider; registered_user is unused but ensures distinct fixtures
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    bad = await api_client.post(
        "/api/v1/rider/me/deliveries/availability",
        headers=logged_in["headers"], json={"status": "offline"},
    )
    assert bad.status_code == 422
    assert "offline" in bad.json()["message"].lower() or "active" in bad.json()["message"].lower()


# ============================================================
# Tasks
# ============================================================


async def test_today_tasks_returns_assigned(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    r = await api_client.get(
        "/api/v1/rider/me/deliveries/tasks", headers=logged_in["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rider_id"] == setup["rider_id"]
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["assignment_id"] == setup["assignment_id"]
    assert item["order_code"] == setup["order_code"]
    assert item["status"] == "assigned"
    assert item["recipient_name"] == "Recipient X"
    assert body["counts"]["assigned"] == 1


async def test_next_task_returns_first_open(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    r = await api_client.get(
        "/api/v1/rider/me/deliveries/tasks/next", headers=logged_in["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body is not None
    assert body["assignment_id"] == setup["assignment_id"]


async def test_next_task_null_when_idle(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    await _onboard_rider(logged_in["user_id"], "R-IDL")
    r = await api_client.get(
        "/api/v1/rider/me/deliveries/tasks/next", headers=logged_in["headers"],
    )
    assert r.status_code == 200
    assert r.json() is None


# ============================================================
# Scan
# ============================================================


async def test_scan_correct_code_returns_ok_true(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    r = await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/scan",
        headers=logged_in["headers"],
        json={"scanned_code": setup["order_code"], "intent": "pickup"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["expected_code"] == setup["order_code"]


async def test_scan_wrong_code_returns_ok_false_with_200(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    r = await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/scan",
        headers=logged_in["headers"],
        json={"scanned_code": "HSO-WRONG-1", "intent": "pickup"},
    )
    # Wrong scan returns 200 ok=false so the rider can re-scan immediately.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["scanned_code"] == "HSO-WRONG-1"


async def test_scan_case_insensitive_and_trim(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    r = await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/scan",
        headers=logged_in["headers"],
        json={"scanned_code": f"  {setup['order_code'].lower()}  ", "intent": "delivery"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_scan_other_riders_assignment_403(
    api_client: AsyncClient, admin_user: dict, logged_in: dict, registered_user: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    # Onboard a second rider linked to admin_user (who has order.fulfill via *).
    await _onboard_rider(admin_user["user_id"], "R-OTHER")
    r = await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/scan",
        headers=admin_user["headers"],
        json={"scanned_code": setup["order_code"], "intent": "pickup"},
    )
    assert r.status_code == 403


# ============================================================
# Signature upload (POD)
# ============================================================


async def test_upload_signature_succeeds_after_pickup(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    # Move to PICKED_UP first.
    pu = await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/pickup",
        headers=logged_in["headers"], json={"notes": None},
    )
    assert pu.status_code == 200, pu.text

    # Upload signature.
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    r = await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/upload-signature",
        headers=logged_in["headers"],
        files={"file": ("sig.png", io.BytesIO(png_bytes), "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["pod_signature_path"] is not None


async def test_upload_signature_rejected_in_assigned_state(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    r = await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/upload-signature",
        headers=logged_in["headers"],
        files={"file": ("sig.png", io.BytesIO(png_bytes), "image/png")},
    )
    assert r.status_code == 422


# ============================================================
# COD summary
# ============================================================


async def test_cod_summary_after_collection(
    api_client: AsyncClient, admin_user: dict, logged_in: dict,
) -> None:
    setup = await _setup_assigned_delivery(api_client, admin_user, admin_user, logged_in)
    # pickup + upload photo + deliver with cod_collected.
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/pickup",
        headers=logged_in["headers"], json={"notes": None},
    )
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/upload-pod",
        headers=logged_in["headers"],
        files={"file": ("pod.png", io.BytesIO(png_bytes), "image/png")},
    )
    deliver = await api_client.post(
        f"/api/v1/rider/me/deliveries/{setup['assignment_id']}/deliver",
        headers=logged_in["headers"],
        json={
            "recipient_name": "Recipient X",
            "pod_otp_verified": False,
            "cod_collected": "200.00",
            "notes": None,
        },
    )
    assert deliver.status_code == 200, deliver.text

    summary = await api_client.get(
        "/api/v1/rider/me/deliveries/cod-summary",
        headers=logged_in["headers"],
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["rider_id"] == setup["rider_id"]
    # 2 units x 100 BDT = 200 collected today.
    from decimal import Decimal
    assert Decimal(body["today_collected_amount"]) == Decimal("200.00")
    assert body["today_collected_count"] == 1
