from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.db.session import get_sessionmaker
from app.core.events.dispatcher import dispatch_once
from app.modules.inventory.models import StockBalance, StockBucket
from app.modules.returns.models import ReturnRequest

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
    client: AsyncClient, headers: dict[str, str], *, name: str,
) -> str:
    payload: dict[str, Any] = {
        "name": name,
        "base_currency": "BDT",
        "status": "active",
        "variants": [{"options": {}, "price": "100.00"}],
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
) -> str:
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
    return resp.json()["lines"][0]["batch_id"]


async def _setup_in_stock(
    api_client: AsyncClient, admin_headers: dict[str, str], *, name: str, qty: int = 10,
) -> tuple[str, str]:
    """Returns (variant_id, batch_id)."""
    variant_id = await _create_variant(api_client, admin_headers, name=name)
    supplier_id = await _create_supplier(api_client, admin_headers, f"S-{name[:6]}")
    batch_id = await _receive(
        api_client, admin_headers,
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number=f"INV-{name[:10]}", quantity=qty,
    )
    return variant_id, batch_id


def _addr() -> dict[str, Any]:
    return {
        "recipient_name": "Returner",
        "phone": "+8801911000077",
        "line1": "House 1",
        "city": "Dhaka",
    }


async def _place_and_complete_order(
    api_client: AsyncClient,
    *,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    variant_id: str,
    quantity: int = 1,
) -> tuple[str, str]:
    """Place a COD order, push it through the entire lifecycle to COMPLETED.
    Returns ``(order_id, order_line_id)``.
    """
    place = await api_client.post(
        "/api/v1/orders",
        headers=customer_headers,
        json={
            "items": [{"variant_id": variant_id, "quantity": quantity}],
            "payment_method": "cod",
            "delivery_address": _addr(),
            "currency": "BDT",
        },
    )
    assert place.status_code == 201, place.text
    body = place.json()
    order_id = body["id"]
    order_line_id = body["lines"][0]["id"]
    h = admin_headers
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/start-packing", headers=h, json={},
    )
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/dispatch", headers=h, json={},
    )
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/complete", headers=h, json={},
    )
    # Drain outbox so the order.completed event reaches the inventory consume
    # handler — which actually drains reserved stock.
    for _ in range(10):
        if await dispatch_once() == 0:
            break
    return order_id, order_line_id


async def _bucket_qty(variant_id: str, bucket: str) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
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


# ---------------- Customer request ----------------


async def test_customer_can_only_return_completed_order(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, _ = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Pending",
    )
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
    order_line_id = place.json()["lines"][0]["id"]
    # Order is in 'approved' state, not 'completed'.
    resp = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "changed mind",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    assert resp.status_code == 422


async def test_customer_creates_return_for_completed_order(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, _ = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-OK",
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )

    resp = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "did not need",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "requested"
    assert body["code"].startswith("RR-")
    assert len(body["lines"]) == 1
    assert body["lines"][0]["requested_quantity"] == 1


async def test_return_quantity_cannot_exceed_ordered(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, _ = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Excess",
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=2,
    )
    resp = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "trying too many",
            "lines": [{"order_line_id": order_line_id, "quantity": 5}],
        },
    )
    assert resp.status_code == 422


async def test_customer_cannot_return_others_order(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    """Admin completes their own order; logged_in (different user) tries to
    open a return → 403.
    """
    variant_id, _ = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Own",
    )
    # Admin places + completes their own order via the full flow.
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=admin_user["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    resp = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "not mine",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    assert resp.status_code == 403


# ---------------- Sealed → restock ----------------


async def test_sealed_condition_restocks_to_available(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, batch_id = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Sealed", qty=10,
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=2,
    )
    # After consume: 10 - 2 = 8 in available.
    assert await _bucket_qty(variant_id, "available") == 8

    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "did not need",
            "lines": [{"order_line_id": order_line_id, "quantity": 2}],
        },
    )
    rr_id = create.json()["id"]
    line_id = create.json()["lines"][0]["id"]
    h = admin_user["headers"]
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/receive", headers=h, json={})
    await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=h,
        json={
            "lines": [
                {
                    "line_id": line_id,
                    "inspected_quantity": 2,
                    "condition": "sealed",
                    "target_batch_id": batch_id,
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    completed = await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/complete", headers=h,
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "completed"
    assert body["lines"][0]["applied_action"] == "restock"

    # Stock returned to available.
    assert await _bucket_qty(variant_id, "available") == 10


# ---------------- Opened → block ----------------


async def test_opened_condition_blocks_stock(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, batch_id = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Opened", qty=5,
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=1,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "took one pill, didn't help",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    line_id = create.json()["lines"][0]["id"]
    h = admin_user["headers"]
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/receive", headers=h, json={})
    await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=h,
        json={
            "lines": [
                {
                    "line_id": line_id,
                    "inspected_quantity": 1,
                    "condition": "opened",
                    "target_batch_id": batch_id,
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/complete", headers=h)

    # Available unchanged at 4 (5 received - 1 consumed). Blocked = 1.
    assert await _bucket_qty(variant_id, "available") == 4
    assert await _bucket_qty(variant_id, "blocked") == 1


# ---------------- Cold-chain broken → block ----------------


async def test_cold_chain_broken_blocks_stock(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, batch_id = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Cold", qty=5,
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=1,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "fridge temperature warning during transit",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    line_id = create.json()["lines"][0]["id"]
    h = admin_user["headers"]
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/receive", headers=h, json={})
    await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=h,
        json={
            "lines": [
                {
                    "line_id": line_id,
                    "inspected_quantity": 1,
                    "condition": "cold_chain_broken",
                    "target_batch_id": batch_id,
                    "target_warehouse_code": "MAIN",
                    "inspection_notes": "rider's cooler bag was warm",
                },
            ],
        },
    )
    completed = await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/complete", headers=h,
    )
    assert completed.json()["lines"][0]["applied_action"] == "block"
    assert await _bucket_qty(variant_id, "blocked") == 1


# ---------------- Expired → disposal ----------------


async def test_expired_condition_routes_to_disposal_bucket(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, batch_id = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Expired", qty=3,
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=1,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "expired before I used it",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    line_id = create.json()["lines"][0]["id"]
    h = admin_user["headers"]
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/receive", headers=h, json={})
    await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=h,
        json={
            "lines": [
                {
                    "line_id": line_id,
                    "inspected_quantity": 1,
                    "condition": "expired",
                    "target_batch_id": batch_id,
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    completed = await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/complete", headers=h,
    )
    assert completed.json()["lines"][0]["applied_action"] == "dispose"
    # Available unchanged. Disposal write-off captured in expired bucket.
    assert await _bucket_qty(variant_id, "available") == 2
    assert await _bucket_qty(variant_id, "expired") == 1


# ---------------- State machine guards ----------------


async def test_inspect_requires_received_state(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, batch_id = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-G1",
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "x",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    line_id = create.json()["lines"][0]["id"]
    # Skip 'receive' — try to inspect directly.
    resp = await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=admin_user["headers"],
        json={
            "lines": [
                {
                    "line_id": line_id,
                    "inspected_quantity": 1,
                    "condition": "sealed",
                    "target_batch_id": batch_id,
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    assert resp.status_code == 422


async def test_inspect_must_cover_every_line(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_a, batch_a = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-G2-A",
    )
    variant_b, batch_b = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-G2-B",
    )
    # Two-line order.
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [
                {"variant_id": variant_a, "quantity": 1},
                {"variant_id": variant_b, "quantity": 1},
            ],
            "payment_method": "cod",
            "delivery_address": _addr(),
            "currency": "BDT",
        },
    )
    body = place.json()
    order_id = body["id"]
    line_a_id = next(li["id"] for li in body["lines"] if li["variant_id"] == variant_a)
    line_b_id = next(li["id"] for li in body["lines"] if li["variant_id"] == variant_b)
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
    for _ in range(10):
        if await dispatch_once() == 0:
            break

    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "x",
            "lines": [
                {"order_line_id": line_a_id, "quantity": 1},
                {"order_line_id": line_b_id, "quantity": 1},
            ],
        },
    )
    rr_id = create.json()["id"]
    line_ids = [li["id"] for li in create.json()["lines"]]
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/receive", headers=h, json={})

    # Inspect only ONE line — should fail.
    resp = await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=h,
        json={
            "lines": [
                {
                    "line_id": line_ids[0],
                    "inspected_quantity": 1,
                    "condition": "sealed",
                    "target_batch_id": batch_a,
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    assert resp.status_code == 422
    _ = batch_b


# ---------------- Cancel / reject ----------------


async def test_customer_cancel_in_requested_state(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, _ = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-CC",
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "x",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    cancel = await api_client.post(
        f"/api/v1/returns/{rr_id}/cancel",
        headers=logged_in["headers"],
        json={"reason": "changed my mind"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"


async def test_admin_reject(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, _ = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-RJ",
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "x",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    reject = await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/reject",
        headers=admin_user["headers"],
        json={"reason": "outside return window"},
    )
    assert reject.status_code == 200
    body = reject.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "outside return window"


# ---------------- Audit + ledger ----------------


async def test_completion_writes_inventory_ledger_with_return_kind(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    from app.modules.inventory.models import LedgerKind, StockLedger

    variant_id, batch_id = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Ledger",
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "test",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    line_id = create.json()["lines"][0]["id"]
    h = admin_user["headers"]
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/receive", headers=h, json={})
    await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=h,
        json={
            "lines": [
                {
                    "line_id": line_id,
                    "inspected_quantity": 1,
                    "condition": "sealed",
                    "target_batch_id": batch_id,
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/complete", headers=h)

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(StockLedger).where(
                        StockLedger.reference_type == "return_request",
                        StockLedger.reference_id == UUID(rr_id),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].kind == LedgerKind.RETURN.value
    assert rows[0].bucket == StockBucket.AVAILABLE.value
    assert rows[0].quantity_delta == 1


# ---------------- RBAC ----------------


async def test_anon_cannot_create_return(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/returns",
        json={
            "order_id": "00000000-0000-0000-0000-000000000000",
            "reason": "x",
            "lines": [
                {
                    "order_line_id": "00000000-0000-0000-0000-000000000000",
                    "quantity": 1,
                },
            ],
        },
    )
    assert resp.status_code == 401


async def test_customer_cannot_inspect(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, batch_id = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-RB",
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "x",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    line_id = create.json()["lines"][0]["id"]
    resp = await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=logged_in["headers"],
        json={
            "lines": [
                {
                    "line_id": line_id,
                    "inspected_quantity": 1,
                    "condition": "sealed",
                    "target_batch_id": batch_id,
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    assert resp.status_code == 403


# ---------------- Full lifecycle history ----------------


async def test_full_lifecycle_writes_history(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    variant_id, batch_id = await _setup_in_stock(
        api_client, admin_user["headers"], name="Ret-Hist",
    )
    order_id, order_line_id = await _place_and_complete_order(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    create = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "x",
            "lines": [{"order_line_id": order_line_id, "quantity": 1}],
        },
    )
    rr_id = create.json()["id"]
    line_id = create.json()["lines"][0]["id"]
    h = admin_user["headers"]
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/receive", headers=h, json={})
    await api_client.post(
        f"/api/v1/admin/returns/{rr_id}/inspect",
        headers=h,
        json={
            "lines": [
                {
                    "line_id": line_id,
                    "inspected_quantity": 1,
                    "condition": "sealed",
                    "target_batch_id": batch_id,
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    await api_client.post(f"/api/v1/admin/returns/{rr_id}/complete", headers=h)

    detail = await api_client.get(
        f"/api/v1/admin/returns/{rr_id}", headers=admin_user["headers"],
    )
    statuses = [h["to_status"] for h in detail.json()["history"]]
    assert statuses == ["requested", "received", "inspected", "completed"]

    sm = get_sessionmaker()
    async with sm() as s:
        rr = (
            (
                await s.execute(
                    select(ReturnRequest).where(ReturnRequest.id == UUID(rr_id))
                )
            )
            .scalars()
            .one()
        )
    assert rr.status == "completed"
    assert rr.received_by == UUID(admin_user["user_id"])
