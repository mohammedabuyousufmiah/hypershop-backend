from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.integration


# ---------------- Fixtures / helpers ----------------


_THREE_IMAGES = [
    {"url": f"https://cdn.hypershop.local/x/{i}.jpg", "kind": "image", "position": i}
    for i in range(3)
]


async def _create_brand(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    resp = await client.post(
        "/api/v1/admin/catalog/brands", headers=headers, json={"name": name}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_product_with_variant(
    client: AsyncClient, headers: dict[str, str], *, name: str, brand_id: str | None = None,
) -> tuple[str, str]:
    """Create an active product with one variant; return (product_id, variant_id)."""
    payload: dict[str, Any] = {
        "name": name,
        "base_currency": "BDT",
        "status": "active",
        "variants": [{"options": {}, "price": "100.00"}],
        "media": _THREE_IMAGES,
    }
    if brand_id:
        payload["brand_id"] = brand_id
    resp = await client.post(
        "/api/v1/admin/catalog/products", headers=headers, json=payload
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
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _receive(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    supplier_id: str,
    variant_id: str,
    invoice_number: str,
    quantity: int = 100,
    expiry: date | None = None,
    batch_number: str = "B-001",
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
                "batch_number": batch_number,
                "expiry_date": (expiry or (date.today() + timedelta(days=180))).isoformat(),
            },
        ],
    }
    resp = await client.post(
        "/api/v1/admin/inventory/goods-receipts", headers=headers, json=body
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------- Suppliers ----------------


async def test_admin_creates_and_lists_suppliers(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    sid = await _create_supplier(api_client, admin_user["headers"], "ACME")
    listing = await api_client.get(
        "/api/v1/admin/inventory/suppliers", headers=admin_user["headers"]
    )
    assert listing.status_code == 200
    assert any(s["id"] == sid for s in listing.json())


async def test_supplier_code_unique(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    await _create_supplier(api_client, admin_user["headers"], "DUP")
    resp = await api_client.post(
        "/api/v1/admin/inventory/suppliers",
        headers=admin_user["headers"],
        json={"code": "DUP", "name": "Other Name"},
    )
    assert resp.status_code == 409


# ---------------- HARD RULE: no stock without invoice ----------------


async def test_receive_creates_available_stock(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    brand = await _create_brand(api_client, admin_user["headers"], "Acme Pharma")
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Bandage Pack", brand_id=brand,
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S001")

    gr = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id, invoice_number="INV-1",
    )
    assert gr["code"].startswith("GR-")

    summary = await api_client.get(
        f"/api/v1/admin/inventory/stock/{variant_id}", headers=admin_user["headers"]
    )
    body = summary.json()
    assert body["by_bucket"]["available"] == 100
    assert body["total"] == 100


async def test_duplicate_supplier_invoice_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Gauze 4x4",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S002")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id, invoice_number="INV-DUP",
    )
    body = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": "INV-DUP",
        "supplier_invoice_date": date.today().isoformat(),
        "warehouse_code": "MAIN",
        "currency": "BDT",
        "lines": [
            {
                "variant_id": variant_id,
                "quantity": 5,
                "unit_cost": "10.00",
                "batch_number": "B-DIFF",
                "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
            },
        ],
    }
    resp = await api_client.post(
        "/api/v1/admin/inventory/goods-receipts",
        headers=admin_user["headers"],
        json=body,
    )
    assert resp.status_code == 409


# ---------------- HARD RULE: batch + expiry mandatory ----------------


async def test_receive_without_batch_fields_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Mask 50pcs",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S003")
    body = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": "INV-NB",
        "supplier_invoice_date": date.today().isoformat(),
        "warehouse_code": "MAIN",
        "currency": "BDT",
        "lines": [
            {"variant_id": variant_id, "quantity": 10, "unit_cost": "1.00"},
        ],
    }
    resp = await api_client.post(
        "/api/v1/admin/inventory/goods-receipts",
        headers=admin_user["headers"],
        json=body,
    )
    assert resp.status_code == 422


async def test_receive_without_expiry_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Syringe 5ml",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S004")
    body = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": "INV-NOEXP",
        "supplier_invoice_date": date.today().isoformat(),
        "warehouse_code": "MAIN",
        "currency": "BDT",
        "lines": [
            {
                "variant_id": variant_id,
                "quantity": 10,
                "unit_cost": "1.00",
                "batch_number": "B-NOEXP",
            },
        ],
    }
    resp = await api_client.post(
        "/api/v1/admin/inventory/goods-receipts",
        headers=admin_user["headers"],
        json=body,
    )
    assert resp.status_code == 422


async def test_receive_into_already_expired_batch_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Glove L",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S005")
    body = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": "INV-EXP",
        "supplier_invoice_date": date.today().isoformat(),
        "warehouse_code": "MAIN",
        "currency": "BDT",
        "lines": [
            {
                "variant_id": variant_id,
                "quantity": 10,
                "unit_cost": "1.00",
                "batch_number": "B-PASTEXP",
                "expiry_date": (date.today() - timedelta(days=1)).isoformat(),
            },
        ],
    }
    resp = await api_client.post(
        "/api/v1/admin/inventory/goods-receipts",
        headers=admin_user["headers"],
        json=body,
    )
    assert resp.status_code == 422


# ---------------- FEFO reservation ----------------


async def test_reserve_chooses_earliest_expiry_first(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="FEFO Tablet",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S006")
    early = date.today() + timedelta(days=30)
    late = date.today() + timedelta(days=400)
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-LATE", quantity=50, expiry=late, batch_number="B-LATE",
    )
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-EARLY", quantity=20, expiry=early, batch_number="B-EARLY",
    )

    resp = await api_client.post(
        f"/api/v1/admin/inventory/stock/{variant_id}/reserve",
        headers=admin_user["headers"],
        json={"quantity": 25},
    )
    assert resp.status_code == 200, resp.text
    allocs = resp.json()["allocations"]
    # First 20 from B-EARLY then 5 from B-LATE.
    qtys_by_batch: dict[str, int] = {}
    for a in allocs:
        qtys_by_batch[a["batch_id"]] = qtys_by_batch.get(a["batch_id"], 0) + a["quantity"]
    assert sum(qtys_by_batch.values()) == 25
    assert max(qtys_by_batch.values()) == 20  # the early batch was drained first


async def test_reserve_rejected_when_insufficient(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Rare Item",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S007")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-TINY", quantity=5,
    )
    resp = await api_client.post(
        f"/api/v1/admin/inventory/stock/{variant_id}/reserve",
        headers=admin_user["headers"],
        json={"quantity": 10},
    )
    assert resp.status_code == 409


async def test_release_returns_to_available(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Releasable",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S008")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-REL", quantity=20,
    )
    res = await api_client.post(
        f"/api/v1/admin/inventory/stock/{variant_id}/reserve",
        headers=admin_user["headers"],
        json={"quantity": 10},
    )
    correlation_id = res.json()["correlation_id"]

    rel = await api_client.post(
        "/api/v1/admin/inventory/stock/release",
        headers=admin_user["headers"],
        json={"correlation_id": correlation_id},
    )
    assert rel.status_code == 204

    summary = await api_client.get(
        f"/api/v1/admin/inventory/stock/{variant_id}", headers=admin_user["headers"]
    )
    body = summary.json()
    assert body["by_bucket"]["available"] == 20
    assert body["by_bucket"]["reserved"] == 0


async def test_consume_drains_reserved(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Consumable",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S009")
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-CON", quantity=15,
    )
    res = await api_client.post(
        f"/api/v1/admin/inventory/stock/{variant_id}/reserve",
        headers=admin_user["headers"],
        json={"quantity": 10},
    )
    correlation_id = res.json()["correlation_id"]
    consume = await api_client.post(
        "/api/v1/admin/inventory/stock/consume",
        headers=admin_user["headers"],
        json={"quantity": 10, "correlation_id": correlation_id},
    )
    assert consume.status_code == 204
    summary = (
        await api_client.get(
            f"/api/v1/admin/inventory/stock/{variant_id}", headers=admin_user["headers"]
        )
    ).json()
    assert summary["by_bucket"]["available"] == 5
    assert summary["by_bucket"]["reserved"] == 0
    assert summary["total"] == 5


# ---------------- Damage / Block / Unblock ----------------


async def test_damage_moves_stock_to_damaged(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Fragile",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S010")
    gr = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-DAM", quantity=10,
    )
    batch_id = gr["lines"][0]["batch_id"]

    resp = await api_client.post(
        f"/api/v1/admin/inventory/stock/{variant_id}/damage",
        headers=admin_user["headers"],
        json={"batch_id": batch_id, "quantity": 3, "reason": "Drop in transit"},
    )
    assert resp.status_code == 204

    summary = (
        await api_client.get(
            f"/api/v1/admin/inventory/stock/{variant_id}", headers=admin_user["headers"]
        )
    ).json()
    assert summary["by_bucket"]["available"] == 7
    assert summary["by_bucket"]["damaged"] == 3


async def test_block_and_unblock_batch(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Blockable",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S011")
    gr = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-BLK", quantity=8,
    )
    batch_id = gr["lines"][0]["batch_id"]

    block = await api_client.post(
        f"/api/v1/admin/inventory/batches/{batch_id}/block",
        headers=admin_user["headers"],
        params={"reason": "Recall"},
    )
    assert block.status_code == 204

    summary_blocked = (
        await api_client.get(
            f"/api/v1/admin/inventory/stock/{variant_id}", headers=admin_user["headers"]
        )
    ).json()
    assert summary_blocked["by_bucket"]["blocked"] == 8
    assert summary_blocked["by_bucket"]["available"] == 0

    unblock = await api_client.post(
        f"/api/v1/admin/inventory/batches/{batch_id}/unblock",
        headers=admin_user["headers"],
    )
    assert unblock.status_code == 204

    summary_unblocked = (
        await api_client.get(
            f"/api/v1/admin/inventory/stock/{variant_id}", headers=admin_user["headers"]
        )
    ).json()
    assert summary_unblocked["by_bucket"]["available"] == 8
    assert summary_unblocked["by_bucket"]["blocked"] == 0


async def test_blocked_batch_excluded_from_reservation(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Has-Blocked",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S012")
    gr_a = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-BB-A", quantity=10, batch_number="B-A",
    )
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-BB-B", quantity=10, batch_number="B-B",
    )
    blocked_batch = gr_a["lines"][0]["batch_id"]
    await api_client.post(
        f"/api/v1/admin/inventory/batches/{blocked_batch}/block",
        headers=admin_user["headers"],
        params={"reason": "QA hold"},
    )

    res = await api_client.post(
        f"/api/v1/admin/inventory/stock/{variant_id}/reserve",
        headers=admin_user["headers"],
        json={"quantity": 10},
    )
    assert res.status_code == 200
    for alloc in res.json()["allocations"]:
        assert alloc["batch_id"] != blocked_batch


# ---------------- Auto-expire ----------------


async def test_expire_overdue_moves_stock_to_expired_bucket(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Soon-To-Expire",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S013")
    # Receive with a near-future expiry, then directly age the batch in DB so
    # we can run the expire job deterministically.
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-AGE", quantity=12,
        expiry=date.today() + timedelta(days=10),
    )

    # Backdate the batch's expiry_date by direct UPDATE — simulating "tomorrow's
    # cron run" without time-travel.
    from sqlalchemy import update as sa_update

    from app.core.db.session import get_sessionmaker
    from app.modules.inventory.models import Batch

    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        await s.execute(
            sa_update(Batch)
            .where(Batch.variant_id == UUID(variant_id))
            .values(expiry_date=date.today() - timedelta(days=1))
        )

    job = await api_client.post(
        "/api/v1/admin/inventory/jobs/expire-overdue",
        headers=admin_user["headers"],
    )
    assert job.status_code == 200
    assert job.json()["batches_expired"] >= 1

    summary = (
        await api_client.get(
            f"/api/v1/admin/inventory/stock/{variant_id}", headers=admin_user["headers"]
        )
    ).json()
    assert summary["by_bucket"]["expired"] == 12
    assert summary["by_bucket"]["available"] == 0


async def test_near_expiry_scan_emits_outbox_event(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Almost-Expired",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S014")
    # Default INVENTORY_NEAR_EXPIRY_DAYS=30 in tests, so 7 days is well inside.
    await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-NE", quantity=5,
        expiry=date.today() + timedelta(days=7),
    )

    job = await api_client.post(
        "/api/v1/admin/inventory/jobs/near-expiry-scan",
        headers=admin_user["headers"],
    )
    assert job.status_code == 200
    assert job.json()["alerts_emitted"] >= 1

    from app.core.db.session import get_sessionmaker
    from app.core.events.models import OutboxMessage

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(OutboxMessage).where(
                        OutboxMessage.type == "inventory.batch.near_expiry"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any(r.payload.get("variant_id") == variant_id for r in rows)


# ---------------- Audit + ledger ----------------


async def test_receipt_writes_ledger_and_audit(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    _, variant_id = await _create_product_with_variant(
        api_client, admin_user["headers"], name="Audit-Watch",
    )
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S015")
    gr = await _receive(
        api_client, admin_user["headers"],
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number="INV-AUD", quantity=4,
    )

    from app.core.audit.models import AuditLog
    from app.core.db.session import get_sessionmaker
    from app.modules.inventory.models import StockLedger

    sm = get_sessionmaker()
    async with sm() as s:
        ledger_rows = (
            (
                await s.execute(
                    select(StockLedger).where(
                        StockLedger.reference_type == "goods_receipt",
                        StockLedger.reference_id == UUID(gr["id"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        audit_rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "inventory.goods_receipt.create",
                        AuditLog.resource_id == gr["id"],
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(ledger_rows) == 1
    assert ledger_rows[0].kind == "receipt"
    assert ledger_rows[0].quantity_delta == 4
    assert len(audit_rows) == 1


# ---------------- RBAC ----------------


async def test_customer_cannot_create_supplier(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/inventory/suppliers",
        headers=logged_in["headers"],
        json={"code": "FORBIDDEN", "name": "Should Fail"},
    )
    assert resp.status_code == 403


async def test_anon_cannot_create_goods_receipt(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/admin/inventory/goods-receipts",
        json={
            "supplier_id": "00000000-0000-0000-0000-000000000000",
            "supplier_invoice_number": "X",
            "supplier_invoice_date": date.today().isoformat(),
            "warehouse_code": "MAIN",
            "currency": "BDT",
            "lines": [
                {
                    "variant_id": "00000000-0000-0000-0000-000000000000",
                    "quantity": 1,
                    "unit_cost": "1.00",
                    "batch_number": "X",
                    "expiry_date": "2099-01-01",
                },
            ],
        },
    )
    assert resp.status_code == 401
