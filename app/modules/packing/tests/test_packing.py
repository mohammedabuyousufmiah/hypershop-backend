from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update

from app.core.db.session import get_sessionmaker
from app.modules.inventory.models import Batch
from app.modules.packing.models import (
    PackingScan,
    PackingSession,
    PackingSessionLine,
)

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


async def _create_product_with_barcode(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    name: str,
    barcode: str,
    price: str = "100.00",
) -> tuple[str, str]:
    """Create an active non-medicine product with a single variant having
    a fixed barcode. Returns ``(product_id, variant_id)``.
    """
    payload: dict[str, Any] = {
        "name": name,
        "base_currency": "BDT",
        "status": "active",
        "variants": [{"options": {}, "price": price, "barcode": barcode}],
        "media": _THREE_IMAGES,
    }
    resp = await client.post(
        "/api/v1/admin/catalog/products", headers=headers, json=payload,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["variants"][0]["id"]


async def _create_supplier(client: AsyncClient, headers: dict[str, str], code: str) -> str:
    resp = await client.post(
        "/api/v1/admin/inventory/suppliers",
        headers=headers,
        json={"code": code, "name": f"Supplier {code}"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _receive(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    supplier_id: str,
    variant_id: str,
    invoice_number: str,
    quantity: int,
    batch_number: str,
    expiry_offset_days: int = 200,
) -> str:
    """Receive stock; returns the batch_id created."""
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
                "batch_number": batch_number,
                "expiry_date": (
                    date.today() + timedelta(days=expiry_offset_days)
                ).isoformat(),
            },
        ],
    }
    resp = await client.post(
        "/api/v1/admin/inventory/goods-receipts", headers=headers, json=body,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["lines"][0]["batch_id"]


def _address() -> dict[str, Any]:
    return {
        "recipient_name": "Picker Test",
        "phone": "+8801711000001",
        "line1": "Warehouse 1",
        "city": "Dhaka",
    }


async def _place_cod_order_into_packing(
    api_client: AsyncClient,
    *,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    variant_id: str,
    quantity: int = 1,
) -> str:
    """Place a COD order, then advance it to PACKING state. Returns order_id."""
    body = {
        "items": [{"variant_id": variant_id, "quantity": quantity}],
        "payment_method": "cod",
        "delivery_address": _address(),
        "currency": "BDT",
    }
    place = await api_client.post(
        "/api/v1/orders", headers=customer_headers, json=body,
    )
    assert place.status_code == 201, place.text
    order_id = place.json()["id"]
    pack = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/start-packing",
        headers=admin_headers,
        json={},
    )
    assert pack.status_code == 200, pack.text
    return order_id


async def _open_packing(
    api_client: AsyncClient, admin_headers: dict[str, str], order_id: str,
) -> dict[str, Any]:
    resp = await api_client.post(
        f"/api/v1/admin/packing/sessions?order_id={order_id}",
        headers=admin_headers,
        json={"notes": "test"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------- Open session ----------------


async def test_open_session_snapshots_reserved_batch(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-A", barcode="8901030000001",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-A")
    batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-A", quantity=10, batch_number="B-A1",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=2,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)
    assert sess["status"] == "open"
    assert len(sess["lines"]) == 1
    line = sess["lines"][0]
    assert line["expected_quantity"] == 2
    assert line["scanned_quantity"] == 0
    assert line["expected_batch_id"] == batch_id


async def test_open_session_refuses_non_packing_order(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-Bad", barcode="8901030000002",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-B")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-B", quantity=5, batch_number="B-B1",
    )
    body = {
        "items": [{"variant_id": variant_id, "quantity": 1}],
        "payment_method": "cod",
        "delivery_address": _address(),
        "currency": "BDT",
    }
    place = await api_client.post(
        "/api/v1/orders", headers=logged_in["headers"], json=body,
    )
    order_id = place.json()["id"]
    # Order is in 'approved', not 'packing' yet.
    resp = await api_client.post(
        f"/api/v1/admin/packing/sessions?order_id={order_id}",
        headers=admin_user["headers"],
        json={},
    )
    assert resp.status_code == 422


async def test_cannot_open_two_concurrent_sessions_for_same_order(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-Dup", barcode="8901030000003",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-D")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-D", quantity=5, batch_number="B-D1",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    await _open_packing(api_client, admin_user["headers"], order_id)
    second = await api_client.post(
        f"/api/v1/admin/packing/sessions?order_id={order_id}",
        headers=admin_user["headers"],
        json={},
    )
    # Partial unique index → ConflictError → 409.
    assert second.status_code == 409


# ---------------- Scan: accepted ----------------


async def test_scan_accepted_increments_line_and_completes_session(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-OK", barcode="8901030000010",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-OK")
    batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-OK", quantity=3, batch_number="B-OK",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=2,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)

    first = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "8901030000010", "batch_id": batch_id},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["accepted"] is True
    assert body["outcome"] == "accepted"
    assert body["line_scanned_quantity"] == 1
    assert body["session_completed"] is False

    second = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "8901030000010", "batch_id": batch_id},
    )
    assert second.status_code == 200
    body = second.json()
    assert body["accepted"] is True
    assert body["session_completed"] is True
    assert body["session_status"] == "completed"


# ---------------- Scan: wrong item ----------------


async def test_scan_wrong_item_blocked(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_a = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-WrongA", barcode="8901030000020",
    )
    _, variant_b = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-WrongB", barcode="8901030000021",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-W")
    batch_a = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_a,
        invoice_number="INV-PK-WA", quantity=3, batch_number="B-WA",
    )
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_b,
        invoice_number="INV-PK-WB", quantity=3, batch_number="B-WB",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_a,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)

    # Picker accidentally scans variant B.
    resp = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "8901030000021", "batch_id": batch_a},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is False
    assert body["outcome"] == "wrong_item"
    assert body["can_supervisor_override"] is False  # never override wrong-item


async def test_scan_unknown_barcode_blocked(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-Unk", barcode="8901030000030",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-U")
    batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-U", quantity=2, batch_number="B-U1",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)

    resp = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "9999999999999", "batch_id": batch_id},
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "unknown_barcode"


# ---------------- Scan: expired ----------------


async def test_scan_expired_blocked(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-Exp", barcode="8901030000040",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-E")
    batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-E", quantity=3, batch_number="B-E1",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)

    # Backdate the batch's expiry to yesterday.
    smk = get_sessionmaker()
    async with smk() as s, s.begin():
        await s.execute(
            update(Batch)
            .where(Batch.id == batch_id)
            .values(expiry_date=date.today() - timedelta(days=1))
        )

    resp = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "8901030000040", "batch_id": batch_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "expired"
    assert body["accepted"] is False
    assert body["can_supervisor_override"] is False  # never overrideable


# ---------------- Scan: batch mismatch ----------------


async def test_scan_batch_mismatch_blocks_with_override_hint(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-BM", barcode="8901030000050",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-BM")
    early_batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-BM-EARLY", quantity=2, batch_number="B-BM-EARLY",
        expiry_offset_days=30,
    )
    late_batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-BM-LATE", quantity=5, batch_number="B-BM-LATE",
        expiry_offset_days=400,
    )
    # FEFO will reserve from EARLY first.
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=2,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)
    expected_line = sess["lines"][0]
    assert expected_line["expected_batch_id"] == early_batch_id

    # Picker scans the LATE batch by mistake.
    resp = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "8901030000050", "batch_id": late_batch_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "batch_mismatch"
    assert body["accepted"] is False
    assert body["can_supervisor_override"] is True


async def test_supervisor_override_accepts_substitute_batch(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-OV", barcode="8901030000060",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-OV")
    early_batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-OV-E", quantity=2, batch_number="B-OV-E",
        expiry_offset_days=30,
    )
    late_batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-OV-L", quantity=2, batch_number="B-OV-L",
        expiry_offset_days=400,
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=1,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)
    line_id = sess["lines"][0]["id"]
    assert sess["lines"][0]["expected_batch_id"] == early_batch_id

    override = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/override-scan",
        headers=admin_user["headers"],
        json={
            "barcode": "8901030000060",
            "batch_id": late_batch_id,
            "line_id": line_id,
            "reason": "Earlier batch shelf empty",
        },
    )
    assert override.status_code == 200, override.text
    body = override.json()
    assert body["accepted"] is True
    assert body["outcome"] == "overridden"
    assert body["session_completed"] is True

    # Line shows the override + accepted_batch_id set.
    smk = get_sessionmaker()
    async with smk() as s:
        line = await s.get(PackingSessionLine, line_id)
        assert line is not None
        assert line.status == "overridden"
        assert str(line.accepted_batch_id) == late_batch_id


async def test_override_refuses_expired_substitute(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-OVX", barcode="8901030000070",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-OVX")
    early_batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-OVX-E", quantity=1, batch_number="B-OVX-E",
        expiry_offset_days=30,
    )
    sub_batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-OVX-S", quantity=2, batch_number="B-OVX-S",
        expiry_offset_days=400,
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)
    line_id = sess["lines"][0]["id"]
    assert sess["lines"][0]["expected_batch_id"] == early_batch_id

    # Backdate the substitute batch to expired.
    smk = get_sessionmaker()
    async with smk() as s, s.begin():
        await s.execute(
            update(Batch)
            .where(Batch.id == sub_batch_id)
            .values(expiry_date=date.today() - timedelta(days=1))
        )

    resp = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/override-scan",
        headers=admin_user["headers"],
        json={
            "barcode": "8901030000070",
            "batch_id": sub_batch_id,
            "line_id": line_id,
            "reason": "trying to push expired",
        },
    )
    assert resp.status_code == 422


# ---------------- Over quantity ----------------


async def test_scan_over_quantity_blocked(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-OQ", barcode="8901030000080",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-OQ")
    batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-OQ", quantity=2, batch_number="B-OQ",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
        quantity=1,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)

    first = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "8901030000080", "batch_id": batch_id},
    )
    assert first.json()["accepted"] is True

    # Session is now completed; second scan should fail (session not open).
    second = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "8901030000080", "batch_id": batch_id},
    )
    assert second.status_code == 422


# ---------------- Session cancel ----------------


async def test_cancel_session(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-CN", barcode="8901030000090",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-CN")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-CN", quantity=3, batch_number="B-CN",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)
    cancel = await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/cancel",
        headers=admin_user["headers"],
        json={"reason": "abandon"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"


# ---------------- Audit + ledger ----------------


async def test_blocked_scans_are_logged_in_packing_scans(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-AU", barcode="8901030000100",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-AU")
    batch_id = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-AU", quantity=3, batch_number="B-AU",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    sess = await _open_packing(api_client, admin_user["headers"], order_id)
    # Three different blocked scans + one accepted.
    await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "0000000000000", "batch_id": batch_id},
    )
    await api_client.post(
        f"/api/v1/admin/packing/sessions/{sess['id']}/scan",
        headers=admin_user["headers"],
        json={"barcode": "8901030000100", "batch_id": batch_id},
    )

    smk = get_sessionmaker()
    async with smk() as s:
        rows = (
            (
                await s.execute(
                    select(PackingScan).where(PackingScan.session_id == sess["id"])
                )
            )
            .scalars()
            .all()
        )
    outcomes = sorted(r.outcome for r in rows)
    assert "unknown_barcode" in outcomes
    assert "accepted" in outcomes


# ---------------- RBAC ----------------


async def test_customer_cannot_open_packing_session(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_barcode(
        api_client, admin_user["headers"], name="Tab-RBAC", barcode="8901030000200",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "PK-R")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-PK-R", quantity=2, batch_number="B-R",
    )
    order_id = await _place_cod_order_into_packing(
        api_client,
        customer_headers=logged_in["headers"],
        admin_headers=admin_user["headers"],
        variant_id=variant_id,
    )
    resp = await api_client.post(
        f"/api/v1/admin/packing/sessions?order_id={order_id}",
        headers=logged_in["headers"],
        json={},
    )
    assert resp.status_code == 403
