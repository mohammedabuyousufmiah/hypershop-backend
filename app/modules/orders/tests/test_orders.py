from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.db.session import get_sessionmaker
from app.modules.orders.models import Order, OrderStatusHistory

pytestmark = pytest.mark.integration


_THREE_IMAGES = [
    {"url": f"https://cdn.hypershop.local/x/{i}.jpg", "kind": "image", "position": i}
    for i in range(3)
]


# ---------------- helpers ----------------


async def _create_brand(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    resp = await client.post(
        "/api/v1/admin/catalog/brands", headers=headers, json={"name": name}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_variant(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    name: str,
    requires_prescription: bool = False,
    is_medicine: bool = False,
    brand_id: str | None = None,
    price: str = "100.00",
) -> str:
    payload: dict[str, Any] = {
        "name": name,
        "base_currency": "BDT",
        "status": "active",
        "variants": [{"options": {}, "price": price}],
        "media": _THREE_IMAGES,
    }
    if is_medicine:
        assert brand_id is not None, "medicine product must have a brand"
        payload.update(
            {
                "is_medicine": True,
                "requires_prescription": requires_prescription,
                "generic_name": "Test Generic",
                "strength": "500mg",
                "brand_id": brand_id,
            }
        )
    elif brand_id:
        payload["brand_id"] = brand_id
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
) -> dict[str, Any]:
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
    return resp.json()


def _address() -> dict[str, Any]:
    return {
        "recipient_name": "Alice Test",
        "phone": "+8801711000000",
        "line1": "House 5, Road 11",
        "city": "Dhaka",
        "country": "BD",
    }


async def _setup_purchasable_variant(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    *,
    name: str,
    quantity: int,
    requires_prescription: bool = False,
    supplier_code: str | None = None,
) -> str:
    """Create a non-medicine product, receive stock, return variant_id."""
    brand = await _create_brand(api_client, admin_headers, f"Brand-{name}")
    variant_id = await _create_variant(
        api_client, admin_headers, name=name,
        is_medicine=requires_prescription,
        requires_prescription=requires_prescription,
        brand_id=brand if requires_prescription else None,
    )
    supplier_id = await _create_supplier(api_client, admin_headers, supplier_code or f"S-{name[:6]}")
    await _receive(
        api_client, admin_headers,
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number=f"INV-{name[:10]}", quantity=quantity,
    )
    return variant_id


async def _bucket_qty(variant_id: UUID, bucket: str) -> int:
    from sqlalchemy import select as sa_select

    from app.modules.inventory.models import StockBalance

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    sa_select(StockBalance).where(
                        StockBalance.variant_id == variant_id,
                        StockBalance.bucket == bucket,
                    )
                )
            )
            .scalars()
            .all()
        )
    return sum(r.quantity for r in rows)


# ---------------- COD: full happy path ----------------


async def test_cod_order_auto_advances_to_approved(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="CODItem", quantity=20,
    )

    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 3}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Non-Rx product → auto-approved after stock_reserved.
    assert body["status"] == "approved"
    assert body["payment_method"] == "cod"
    assert body["requires_prescription"] is False
    # 3 × 100 unit + 50 BDT shipping (Dhaka Metro service-area zone)
    assert body["subtotal"] == "300.00"
    assert body["shipping_total"] == "50.00"
    assert body["grand_total"] == "350.00"
    assert body["payment_confirmed_at"] is not None
    assert body["approved_at"] is not None
    assert body["code"].startswith("HSO-")

    # History recorded each step.
    history_targets = [h["to_status"] for h in body["history"]]
    assert history_targets == [
        "payment_confirmed",  # initial placement (COD)
        "stock_reserved",
        "approved",
    ]

    # Inventory reflects the reservation.
    assert await _bucket_qty(UUID(variant_id), "available") == 17
    assert await _bucket_qty(UUID(variant_id), "reserved") == 3


async def test_cod_full_lifecycle_to_completed(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="LifecycleItem", quantity=10,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 4}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    assert place.status_code == 201
    order_id = place.json()["id"]
    h = admin_user["headers"]

    pack = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/start-packing", headers=h, json={},
    )
    assert pack.status_code == 200
    assert pack.json()["status"] == "packing"

    dispatch = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/dispatch", headers=h, json={},
    )
    assert dispatch.status_code == 200
    assert dispatch.json()["status"] == "out_for_delivery"
    assert dispatch.json()["dispatched_at"] is not None

    complete = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/complete", headers=h, json={},
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "completed"

    # Drain outbox so the inventory consume handler runs.
    from app.core.events.dispatcher import dispatch_once
    for _ in range(5):
        if await dispatch_once() == 0:
            break

    # 10 received, 4 consumed → available 6, reserved 0.
    assert await _bucket_qty(UUID(variant_id), "available") == 6
    assert await _bucket_qty(UUID(variant_id), "reserved") == 0


# ---------------- Online: pending → confirm-payment ----------------


async def test_online_order_starts_pending_payment(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="OnlineItem", quantity=5,
    )
    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 2}],
            "payment_method": "online",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending_payment"
    assert body["payment_confirmed_at"] is None

    # Stock NOT yet reserved — online orders only reserve on payment confirmation.
    assert await _bucket_qty(UUID(variant_id), "available") == 5
    assert await _bucket_qty(UUID(variant_id), "reserved") == 0


async def test_online_payment_confirmation_reserves_and_approves(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="OnlinePay", quantity=8,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 3}],
            "payment_method": "online",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]

    confirm = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/confirm-payment",
        headers=admin_user["headers"],
        json={},
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["status"] == "approved"

    assert await _bucket_qty(UUID(variant_id), "available") == 5
    assert await _bucket_qty(UUID(variant_id), "reserved") == 3


# ---------------- Insufficient stock → FAILED ----------------


async def test_cod_insufficient_stock_marks_failed(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="ScarceItem", quantity=2,
    )
    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 50}],  # too many
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    targets = [h["to_status"] for h in body["history"]]
    assert "payment_confirmed" in targets
    assert "failed" in targets

    # Stock untouched — savepoint rolled back.
    assert await _bucket_qty(UUID(variant_id), "available") == 2
    assert await _bucket_qty(UUID(variant_id), "reserved") == 0


# ---------------- requires_prescription telemetry flag ----------------
# Hypershop is a pure e-commerce marketplace — the catalog flag is
# carried through for telemetry but does NOT gate the order flow.
# Orders with Rx items auto-approve just like any other order.


async def test_rx_flagged_order_still_auto_approves(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="RxItem", quantity=10,
        requires_prescription=True,
    )
    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 2}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # No pharmacy gate — the COD lifecycle takes paid orders straight
    # to ``approved`` via stock_reserved. The Rx telemetry flag is
    # still echoed in the response for downstream analytics.
    assert body["status"] == "approved"
    assert body["requires_prescription"] is True
    assert body["lines"][0]["requires_prescription"] is True


# ---------------- State machine guards ----------------


async def test_cannot_dispatch_before_packing(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="GuardItem", quantity=5,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]

    resp = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/dispatch",
        headers=admin_user["headers"],
        json={},
    )
    # approved → out_for_delivery is not in the transition table.
    assert resp.status_code == 422
    assert resp.json()["code"] == "business_rule_violation"


async def test_cannot_complete_from_approved(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="CompleteGuard", quantity=5,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]
    resp = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/complete",
        headers=admin_user["headers"],
        json={},
    )
    assert resp.status_code == 422


# ---------------- Cancellation ----------------


async def test_customer_can_cancel_before_packing(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="CustCancel", quantity=10,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 4}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]

    cancel = await api_client.post(
        f"/api/v1/orders/{order_id}/cancel",
        headers=logged_in["headers"],
        json={"reason": "Changed my mind"},
    )
    assert cancel.status_code == 200
    body = cancel.json()
    assert body["status"] == "cancelled"
    assert body["cancellation_reason"] == "Changed my mind"

    # Outbox release event runs → stock returns to available.
    from app.core.events.dispatcher import dispatch_once
    for _ in range(5):
        if await dispatch_once() == 0:
            break
    assert await _bucket_qty(UUID(variant_id), "available") == 10
    assert await _bucket_qty(UUID(variant_id), "reserved") == 0


async def test_customer_cannot_cancel_after_packing(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="LateCancel", quantity=5,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/start-packing",
        headers=admin_user["headers"], json={},
    )

    resp = await api_client.post(
        f"/api/v1/orders/{order_id}/cancel",
        headers=logged_in["headers"],
        json={"reason": "Too late"},
    )
    assert resp.status_code == 422


async def test_admin_can_cancel_anywhere(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="AdminCancel", quantity=5,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/start-packing",
        headers=admin_user["headers"], json={},
    )
    cancel = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/cancel",
        headers=admin_user["headers"],
        json={"reason": "Admin override"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"


# ---------------- Authorization ----------------


async def test_customer_cannot_view_others_order(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="NotYours", quantity=5,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]

    # Admin's own customer-style fetch is denied (different user).
    resp = await api_client.get(
        f"/api/v1/orders/{order_id}", headers=admin_user["headers"],
    )
    assert resp.status_code == 403


async def test_anon_cannot_place_order(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/orders",
        json={
            "items": [{"variant_id": "00000000-0000-0000-0000-000000000000", "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
        },
    )
    assert resp.status_code == 401


async def test_customer_cannot_use_admin_endpoints(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    resp = await api_client.get(
        "/api/v1/admin/orders", headers=logged_in["headers"],
    )
    assert resp.status_code == 403


# ---------------- Audit + history table ----------------


async def test_each_transition_writes_history_and_audit(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="AuditPath", quantity=3,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = UUID(place.json()["id"])
    h = admin_user["headers"]
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/start-packing", headers=h, json={},
    )
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/dispatch", headers=h, json={},
    )
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/complete", headers=h, json={},
    )

    sm = get_sessionmaker()
    async with sm() as s:
        history = (
            (
                await s.execute(
                    select(OrderStatusHistory)
                    .where(OrderStatusHistory.order_id == order_id)
                    .order_by(OrderStatusHistory.transitioned_at)
                )
            )
            .scalars()
            .all()
        )
        audit = (
            (
                await s.execute(
                    select(AuditLog)
                    .where(AuditLog.resource_id == str(order_id))
                    .order_by(AuditLog.occurred_at)
                )
            )
            .scalars()
            .all()
        )
    statuses = [row.to_status for row in history]
    assert statuses == [
        "payment_confirmed",
        "stock_reserved",
        "approved",
        "packing",
        "out_for_delivery",
        "completed",
    ]
    audit_actions = [row.action for row in audit]
    assert "orders.order.create" in audit_actions
    assert any(
        a == "orders.order.transition.completed" for a in audit_actions
    )


async def test_failed_path_writes_history_and_no_reservation(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="FailHist", quantity=1,
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 99}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = UUID(place.json()["id"])
    sm = get_sessionmaker()
    async with sm() as s:
        order = (
            await s.execute(select(Order).where(Order.id == order_id))
        ).scalar_one()
    assert order.status == "failed"

    # Inventory ledger should have NO rows for this order_id (rolled back).
    from app.modules.inventory.models import StockLedger
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(StockLedger).where(StockLedger.correlation_id == order_id)
                )
            )
            .scalars()
            .all()
        )
    assert rows == []


# ---------------- Validation ----------------


async def test_rejects_inactive_variant(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    """Cannot order a draft product."""
    payload = {
        "name": "Draft Tee",
        "base_currency": "BDT",
        "status": "draft",
        "variants": [{"options": {}, "price": "100.00"}],
        "media": _THREE_IMAGES[:1],
    }
    create = await api_client.post(
        "/api/v1/admin/catalog/products", headers=admin_user["headers"], json=payload,
    )
    assert create.status_code == 201
    variant_id = create.json()["variants"][0]["id"]

    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    assert resp.status_code == 422


async def test_rejects_zero_quantity(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [
                {"variant_id": "00000000-0000-0000-0000-000000000000", "quantity": 0},
            ],
            "payment_method": "cod",
            "delivery_address": _address(),
        },
    )
    assert resp.status_code == 422


# ---------------- Shipping zones (Module 27) ----------------


async def test_shipping_zone_resolves_to_dhaka_metro_default(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    """Dhaka metro address → service_area zone @ 50 BDT shipping."""
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="ZoneA", quantity=10,
    )
    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": {
                "recipient_name": "Z A",
                "phone": "+8801711000010",
                "line1": "House 1",
                "city": "Dhanmondi",
                "country": "BD",
            },
            "currency": "BDT",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["subtotal"] == "100.00"
    assert body["shipping_total"] == "50.00"
    assert body["grand_total"] == "150.00"


async def test_shipping_zone_resolves_to_3pl_for_savar(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    """Savar address matches the 3pl city list → 100 BDT shipping."""
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="ZoneB", quantity=10,
    )
    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": {
                "recipient_name": "Z B",
                "phone": "+8801711000011",
                "line1": "House 1",
                "city": "Savar",
                "country": "BD",
            },
            "currency": "BDT",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["subtotal"] == "100.00"
    assert body["shipping_total"] == "100.00"
    assert body["grand_total"] == "200.00"


async def test_shipping_zone_falls_back_to_default_for_unknown_city(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    """Unknown city → falls back to is_default zone (Dhaka Metro @ 50)."""
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="ZoneC", quantity=10,
    )
    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": {
                "recipient_name": "Z C",
                "phone": "+8801711000012",
                "line1": "House 1",
                # Invented city — matches no zone, expect default fallback.
                "city": "Atlantis-By-The-Sea",
                "country": "BD",
            },
            "currency": "BDT",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["shipping_total"] == "50.00"  # default zone is Dhaka Metro @ 50


async def test_shipping_zone_case_insensitive_city_match(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    """Matching is case-insensitive — 'savar' lower-case still matches the zone."""
    variant_id = await _setup_purchasable_variant(
        api_client, admin_user["headers"], name="ZoneD", quantity=10,
    )
    resp = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": {
                "recipient_name": "Z D",
                "phone": "+8801711000013",
                "line1": "House 1",
                "city": "savar",  # lowercase — DB zone has 'Savar' canonical
                "country": "BD",
            },
            "currency": "BDT",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["shipping_total"] == "100.00"
