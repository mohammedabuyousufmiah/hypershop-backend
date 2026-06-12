from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.db.session import get_sessionmaker
from app.core.events.dispatcher import dispatch_once
from app.modules.deliveries.models import DeliveryAssignment
from app.modules.inventory.models import StockBalance

pytestmark = pytest.mark.integration


_THREE_IMAGES = [
    {"url": f"https://cdn.hypershop.local/x/{i}.jpg", "kind": "image", "position": i}
    for i in range(3)
]


# ---------------- helpers ----------------


async def _create_brand(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    resp = await client.post(
        "/api/v1/admin/catalog/brands", headers=headers, json={"name": name},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_variant(
    client: AsyncClient, headers: dict[str, str], *, name: str, price: str = "100.00",
) -> str:
    payload: dict[str, Any] = {
        "name": name,
        "base_currency": "BDT",
        "status": "active",
        "variants": [{"options": {}, "price": price}],
        "media": _THREE_IMAGES,
    }
    resp = await client.post(
        "/api/v1/admin/catalog/products", headers=headers, json=payload,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["variants"][0]["id"]


async def _create_supplier(client: AsyncClient, headers: dict[str, str], code: str) -> str:
    resp = await client.post(
        "/api/v1/admin/inventory/suppliers",
        headers=headers,
        json={"code": code, "name": f"Supplier {code}"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _receive(
    client: AsyncClient, headers: dict[str, str],
    *, supplier_id: str, variant_id: str, invoice_number: str, quantity: int,
) -> None:
    body = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": invoice_number,
        "supplier_invoice_date": date.today().isoformat(),
        "warehouse_code": "MAIN",
        "currency": "BDT",
        "lines": [
            {
                "variant_id": variant_id,
                "quantity": quantity,
                "unit_cost": "10.00",
                "batch_number": f"B-{invoice_number}",
                "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
            },
        ],
    }
    resp = await client.post(
        "/api/v1/admin/inventory/goods-receipts", headers=headers, json=body,
    )
    assert resp.status_code == 201, resp.text


def _addr() -> dict[str, Any]:
    return {
        "recipient_name": "Patient X",
        "phone": "+8801711000099",
        "line1": "House 1",
        "city": "Dhaka",
    }


async def _place_order_to_out_for_delivery(
    api_client: AsyncClient,
    *,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    variant_id: str,
    payment_method: str = "cod",
    quantity: int = 1,
) -> str:
    place = await api_client.post(
        "/api/v1/orders",
        headers=customer_headers,
        json={
            "items": [{"variant_id": variant_id, "quantity": quantity}],
            "payment_method": payment_method,
            "delivery_address": _addr(),
            "currency": "BDT",
        },
    )
    assert place.status_code == 201, place.text
    order_id = place.json()["id"]
    if payment_method == "online":
        confirm = await api_client.post(
            f"/api/v1/admin/orders/{order_id}/confirm-payment",
            headers=admin_headers,
            json={},
        )
        assert confirm.status_code == 200
    pack = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/start-packing",
        headers=admin_headers,
        json={},
    )
    assert pack.status_code == 200
    dispatch = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/dispatch",
        headers=admin_headers,
        json={},
    )
    assert dispatch.status_code == 200
    return order_id


async def _onboard_rider_for_user(user_id: str, code: str) -> str:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        result = await s.execute(
            text(
                """
                INSERT INTO riders (code, name, phone, vehicle_type, linked_user_id, is_active)
                VALUES (:c, 'Rider Test', '+8801911000001', 'motorcycle', :u, true)
                RETURNING id
                """,
            ),
            {"c": code, "u": user_id},
        )
        return str(result.scalar_one())


async def _bucket_qty(variant_id: str, bucket: str) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        from uuid import UUID

        rows = (
            (
                await s.execute(
                    select(StockBalance).where(
                        StockBalance.variant_id == UUID(variant_id),
                        StockBalance.bucket == bucket,
                    )
                )
            )
            .scalars()
            .all()
        )
    return sum(r.quantity for r in rows)


async def _setup_in_stock(
    api_client: AsyncClient, admin_headers: dict[str, str], *, name: str, qty: int = 10,
) -> str:
    variant_id = await _create_variant(api_client, admin_headers, name=name)
    supplier_id = await _create_supplier(api_client, admin_headers, f"S-{name[:6]}")
    await _receive(
        api_client, admin_headers,
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number=f"INV-{name[:10]}", quantity=qty,
    )
    return variant_id


async def _drain_outbox() -> int:
    total = 0
    for _ in range(20):
        n = await dispatch_once()
        if n == 0:
            break
        total += n
    return total


# ---------------- Rider admin ----------------


async def test_admin_creates_rider(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/deliveries/riders",
        headers=admin_user["headers"],
        json={
            "name": "Rider 1",
            "phone": "+8801911740672",
            "vehicle_type": "motorcycle",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["code"].startswith("RD-")


async def test_customer_cannot_create_rider(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/deliveries/riders",
        headers=logged_in["headers"],
        json={
            "name": "Hacker",
            "phone": "+8801911000000",
            "vehicle_type": "motorcycle",
        },
    )
    assert resp.status_code == 403


# ---------------- Assign ----------------


async def test_assign_requires_out_for_delivery(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="A1")
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _addr(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]
    rider_id = await _onboard_rider_for_user(
        admin_user["user_id"], "RD-NOTOFD",
    )
    resp = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    # Order is in 'approved', not 'out_for_delivery'.
    assert resp.status_code == 422


async def test_assign_cod_pre_fills_cod_expected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="A2")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=2,  # 2 × 100 = 200 BDT
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-A2")
    resp = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "assigned"
    assert body["payment_method"] == "cod"
    assert Decimal(body["cod_expected"]) == Decimal("200.00")
    assert body["cod_status"] == "pending"


async def test_cannot_assign_twice_for_same_order(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="A3")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-A3")
    first = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    assert first.status_code == 201
    second = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    assert second.status_code == 409


# ---------------- Pickup ----------------


async def test_rider_pickup_transitions_to_picked_up(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="P1")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-P1")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    pickup = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={"notes": "got it"},
    )
    assert pickup.status_code == 200
    assert pickup.json()["status"] == "picked_up"
    assert pickup.json()["picked_up_at"] is not None


# ---------------- POD mandatory ----------------


async def test_deliver_without_pod_blocked(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="POD1")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-POD1")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    # Try delivering with no POD evidence at all.
    resp = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Patient X",
            "pod_otp_verified": False,
            "cod_collected": "100.00",
        },
    )
    assert resp.status_code == 422
    assert "POD" in resp.json()["message"]


async def test_deliver_with_otp_evidence_succeeds(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="POD2")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-POD2")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    resp = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Patient X",
            "pod_otp_verified": True,
            "cod_collected": "100.00",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"  # auto-advanced because COD reconciled
    assert body["pod_otp_verified_at"] is not None
    assert body["pod_recipient_name"] == "Patient X"


async def test_deliver_with_photo_evidence_succeeds(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
    tiny_jpeg: bytes,
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="POD3")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-POD3")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    upload = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/upload-pod",
        headers=admin_user["headers"],
        files={"file": ("pod.jpg", tiny_jpeg, "image/jpeg")},
    )
    assert upload.status_code == 200
    assert upload.json()["pod_photo_path"] is not None

    deliver = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Photo POD",
            "cod_collected": "100.00",
        },
    )
    assert deliver.status_code == 200
    assert deliver.json()["status"] == "completed"


# ---------------- COD reconciliation ----------------


async def test_cod_exact_match_auto_reconciles_and_completes(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="C1")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=2,  # 200 BDT
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-C1")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    deliver = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Patient X",
            "pod_otp_verified": True,
            "cod_collected": "200.00",  # exact
        },
    )
    assert deliver.status_code == 200
    body = deliver.json()
    assert body["cod_status"] == "reconciled"
    assert body["status"] == "completed"


async def test_cod_discrepancy_blocks_completion_until_reconcile(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="C2")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=2,  # expected 200
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-C2")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    deliver = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Patient X",
            "pod_otp_verified": True,
            "cod_collected": "180.00",  # short by 20
        },
    )
    assert deliver.status_code == 200
    body = deliver.json()
    assert body["cod_status"] == "discrepancy"
    # Not auto-completed — sits in DELIVERED.
    assert body["status"] == "delivered"

    # Supervisor reconciles; delivery completes.
    rec = await api_client.post(
        f"/api/v1/admin/deliveries/{aid}/reconcile-cod",
        headers=admin_user["headers"],
        json={"resolution_notes": "20 BDT short, customer paid balance later"},
    )
    assert rec.status_code == 200
    body = rec.json()
    assert body["cod_status"] == "resolved"
    assert body["status"] == "completed"


async def test_cod_collected_required_for_cod_orders(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="C3")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-C3")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    resp = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Patient X",
            "pod_otp_verified": True,
            # cod_collected omitted
        },
    )
    assert resp.status_code == 422


async def test_cod_collected_rejected_for_online_orders(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="C4")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        payment_method="online",
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-C4")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    body = create.json()
    assert body["cod_status"] == "n/a"
    aid = body["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    resp = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Patient X",
            "pod_otp_verified": True,
            "cod_collected": "100.00",
        },
    )
    assert resp.status_code == 422


async def test_online_order_completes_without_cod(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="C5")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        payment_method="online",
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-C5")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    deliver = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Patient X",
            "pod_otp_verified": True,
        },
    )
    assert deliver.status_code == 200
    assert deliver.json()["status"] == "completed"
    assert deliver.json()["cod_status"] == "n/a"


# ---------------- Delivery → stock deduct ----------------


async def test_completion_deducts_stock_via_outbox(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    """End-to-end: delivery completion calls OrderService.complete inline,
    which emits ``orders.order.completed``. The inventory handler consumes
    that event → reserved stock is drained.
    """
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="ST1", qty=10)
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=3,
    )
    # At this point: 10 received, 3 reserved, 7 available.
    assert await _bucket_qty(variant_id, "available") == 7
    assert await _bucket_qty(variant_id, "reserved") == 3

    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-ST1")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    deliver = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Stock Test",
            "pod_otp_verified": True,
            "cod_collected": "300.00",
        },
    )
    assert deliver.status_code == 200
    assert deliver.json()["status"] == "completed"

    # Drain outbox so the inventory consume handler runs.
    await _drain_outbox()

    assert await _bucket_qty(variant_id, "available") == 7  # unchanged
    assert await _bucket_qty(variant_id, "reserved") == 0  # drained


# ---------------- State machine guards ----------------


async def test_cannot_deliver_before_pickup(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="G1")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-G1")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    resp = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "X",
            "pod_otp_verified": True,
            "cod_collected": "100.00",
        },
    )
    assert resp.status_code == 422


async def test_admin_cancel_marks_cancelled(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="G2")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-G2")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    cancel = await api_client.post(
        f"/api/v1/admin/deliveries/{aid}/cancel",
        headers=admin_user["headers"],
        json={"reason": "rider sick"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"


async def test_rider_fails_after_pickup(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="G3")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-G3")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    fail = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/fail",
        headers=admin_user["headers"],
        json={"reason": "address not found"},
    )
    assert fail.status_code == 200
    assert fail.json()["status"] == "failed"


# ---------------- Rider RBAC ----------------


async def test_rider_endpoints_require_doctor_record(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    """Admin without a Rider row → 403 on rider endpoints despite RBAC pass."""
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="R1")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    # Create rider linked to a different user (not admin).
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(
            text(
                """
                INSERT INTO riders (code, name, phone, vehicle_type, is_active)
                VALUES ('RD-OTHER', 'Other', '+8801911000099', 'motorcycle', true)
                """,
            ),
        )
        result = await s.execute(text("SELECT id FROM riders WHERE code='RD-OTHER'"))
        rider_id = str(result.scalar_one())
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    # Admin tries to pickup as if they were the rider — but they're not linked.
    pickup = await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    assert pickup.status_code == 403


async def test_anon_cannot_assign(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        json={
            "order_id": "00000000-0000-0000-0000-000000000000",
            "rider_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert resp.status_code == 401


# ---------------- Audit ----------------


async def test_lifecycle_writes_history_and_audit(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_in_stock(api_client, admin_user["headers"], name="AU1")
    order_id = await _place_order_to_out_for_delivery(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    rider_id = await _onboard_rider_for_user(admin_user["user_id"], "RD-AU")
    create = await api_client.post(
        "/api/v1/admin/deliveries/assignments",
        headers=admin_user["headers"],
        json={"order_id": order_id, "rider_id": rider_id},
    )
    aid = create.json()["id"]
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/pickup",
        headers=admin_user["headers"],
        json={},
    )
    await api_client.post(
        f"/api/v1/rider/me/deliveries/{aid}/deliver",
        headers=admin_user["headers"],
        json={
            "recipient_name": "Audit",
            "pod_otp_verified": True,
            "cod_collected": "100.00",
        },
    )
    sm = get_sessionmaker()
    async with sm() as s:
        from uuid import UUID

        a = (
            await s.execute(
                select(DeliveryAssignment).where(
                    DeliveryAssignment.id == UUID(aid),
                )
            )
        ).scalar_one()
        await s.refresh(a, attribute_names=("history",))
        statuses = [h.to_status for h in a.history]
    assert statuses == ["assigned", "picked_up", "delivered", "completed"]
