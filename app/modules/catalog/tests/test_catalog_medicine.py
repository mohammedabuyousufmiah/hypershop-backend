from __future__ import annotations

import re
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


_MEDIA_THREE = [
    {
        "url": "https://cdn.hypershop.local/m/napa-1.jpg",
        "alt": "Front",
        "kind": "image",
        "position": 0,
    },
    {
        "url": "https://cdn.hypershop.local/m/napa-2.jpg",
        "alt": "Back",
        "kind": "image",
        "position": 1,
    },
    {
        "url": "https://cdn.hypershop.local/m/napa-3.jpg",
        "alt": "Box",
        "kind": "image",
        "position": 2,
    },
]


async def _create_brand(api_client: AsyncClient, headers: dict[str, str], name: str) -> str:
    resp = await api_client.post(
        "/api/v1/admin/catalog/brands",
        headers=headers,
        json={"name": name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _med_payload(brand_id: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "name": "Napa 500mg Tablet",
        "short_description": "Paracetamol 500mg",
        "base_currency": "BDT",
        "status": "active",
        "is_medicine": True,
        "requires_prescription": False,
        "generic_name": "Paracetamol",
        "strength": "500mg",
        "dosage_form": "Tablet",
        "brand_id": brand_id,
        "variants": [
            {
                "options": {"pack": "10s"},
                "price": "12.00",
                "barcode": "8901030865278",
            },
            {
                "options": {"pack": "20s"},
                "price": "23.00",
            },
        ],
        "media": _MEDIA_THREE,
    }
    base.update(overrides)
    return base


# ---------------- SKU auto-generation ----------------


async def test_mother_sku_auto_generated_and_variant_skus_derived(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Beximco")
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_med_payload(brand_id),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert re.match(r"^HS-[A-Z2-9]{8}$", body["mother_sku"]), body["mother_sku"]

    skus = sorted(v["sku"] for v in body["variants"])
    assert skus[0] == f"{body['mother_sku']}-V001"
    assert skus[1] == f"{body['mother_sku']}-V002"


async def test_explicit_variant_sku_is_preserved(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Square Pharma")
    payload = _med_payload(
        brand_id,
        variants=[
            {
                "sku": "SQ-NAPA-10",
                "options": {"pack": "10s"},
                "price": "12.00",
            },
            {
                "options": {"pack": "20s"},
                "price": "23.00",
            },
        ],
    )
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 201
    body = resp.json()
    skus = {v["sku"] for v in body["variants"]}
    assert "SQ-NAPA-10" in skus
    auto = [s for s in skus if s != "SQ-NAPA-10"][0]
    assert auto.startswith(body["mother_sku"] + "-V")


# ---------------- Medicine-required fields ----------------


async def test_medicine_without_generic_or_strength_rejected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Renata")
    payload = _med_payload(brand_id)
    payload.pop("generic_name")
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 422


async def test_medicine_without_brand_rejected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    payload = _med_payload(
        brand_id="00000000-0000-0000-0000-000000000000",
    )
    payload["brand_id"] = None
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 422


async def test_medicine_without_explicit_prescription_flag_rejected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Incepta")
    payload = _med_payload(brand_id)
    payload.pop("requires_prescription")
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 422


async def test_prescription_flag_persists(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Acme Rx")
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_med_payload(brand_id, requires_prescription=True, name="Tramadol 50mg"),
    )
    assert resp.status_code == 201
    assert resp.json()["requires_prescription"] is True
    detail = await api_client.get("/api/v1/catalog/products/tramadol-50mg")
    assert detail.status_code == 200
    assert detail.json()["requires_prescription"] is True


# ---------------- Minimum 3 images rule ----------------


async def test_active_product_with_two_images_rejected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Opsonin")
    payload = _med_payload(brand_id)
    payload["media"] = _MEDIA_THREE[:2]
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "business_rule_violation"


async def test_draft_product_can_have_fewer_than_three_images(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Healthcare Pharma")
    payload = _med_payload(brand_id, status="draft")
    payload["media"] = _MEDIA_THREE[:1]
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "draft"


async def test_promoting_draft_to_active_requires_three_images(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Eskayef")
    payload = _med_payload(brand_id, status="draft")
    payload["media"] = _MEDIA_THREE[:2]
    create = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    pid = create.json()["id"]
    resp = await api_client.patch(
        f"/api/v1/admin/catalog/products/{pid}",
        headers=admin_user["headers"],
        json={"status": "active"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "business_rule_violation"


# ---------------- Barcode validation ----------------


async def test_barcode_invalid_chars_rejected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Aristopharma")
    payload = _med_payload(brand_id)
    payload["variants"][0]["barcode"] = "abc 123!!"
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 422


async def test_duplicate_barcode_rejected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "ACI")
    payload_a = _med_payload(brand_id, name="Med A")
    payload_a["variants"][0]["barcode"] = "8901030865900"
    a = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload_a,
    )
    assert a.status_code == 201

    payload_b = _med_payload(brand_id, name="Med B")
    payload_b["variants"][0]["barcode"] = "8901030865900"
    b = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload_b,
    )
    assert b.status_code == 409


# ---------------- Block / Expire visibility ----------------


async def test_blocked_product_hidden_from_public(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Sanofi")
    create = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_med_payload(brand_id, name="Block Me"),
    )
    pid = create.json()["id"]

    listing = await api_client.get("/api/v1/catalog/products?q=Block")
    assert listing.json()["total"] == 1

    block = await api_client.post(
        f"/api/v1/admin/catalog/products/{pid}/block",
        headers=admin_user["headers"],
        json={"reason": "Recall: contamination batch CX-991"},
    )
    assert block.status_code == 200
    body = block.json()
    assert body["blocked_at"] is not None
    assert body["blocked_reason"].startswith("Recall:")

    listing_after = await api_client.get("/api/v1/catalog/products?q=Block")
    assert listing_after.json()["total"] == 0

    detail = await api_client.get("/api/v1/catalog/products/block-me")
    assert detail.status_code == 404

    unblock = await api_client.post(
        f"/api/v1/admin/catalog/products/{pid}/unblock",
        headers=admin_user["headers"],
    )
    assert unblock.status_code == 200
    assert unblock.json()["blocked_at"] is None

    listing_unblocked = await api_client.get("/api/v1/catalog/products?q=Block")
    assert listing_unblocked.json()["total"] == 1


async def test_expired_product_hidden_from_public(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "Pfizer")
    create = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_med_payload(brand_id, name="Expired Soon"),
    )
    pid = create.json()["id"]

    listing = await api_client.get("/api/v1/catalog/products?q=Expired")
    assert listing.json()["total"] == 1

    past = "2020-01-01T00:00:00Z"
    expiry = await api_client.put(
        f"/api/v1/admin/catalog/products/{pid}/expiry",
        headers=admin_user["headers"],
        json={"expires_at": past},
    )
    assert expiry.status_code == 200
    assert expiry.json()["expires_at"].startswith("2020-01-01")

    listing_after = await api_client.get("/api/v1/catalog/products?q=Expired")
    assert listing_after.json()["total"] == 0

    detail = await api_client.get("/api/v1/catalog/products/expired-soon")
    assert detail.status_code == 404


async def test_cannot_create_active_product_with_past_expiry(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand_id = await _create_brand(api_client, admin_user["headers"], "GSK")
    payload = _med_payload(
        brand_id,
        name="Born Expired",
        expires_at="2020-01-01T00:00:00Z",
    )
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "business_rule_violation"


# ---------------- Audit ----------------


async def test_block_unblock_emit_audit_rows(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    from sqlalchemy import select

    from app.core.audit.models import AuditLog
    from app.core.db.session import get_sessionmaker

    brand_id = await _create_brand(api_client, admin_user["headers"], "Servier")
    create = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_med_payload(brand_id, name="Audit Subject"),
    )
    pid = create.json()["id"]
    await api_client.post(
        f"/api/v1/admin/catalog/products/{pid}/block",
        headers=admin_user["headers"],
        json={"reason": "Quality hold"},
    )
    await api_client.post(
        f"/api/v1/admin/catalog/products/{pid}/unblock",
        headers=admin_user["headers"],
    )

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog)
                    .where(AuditLog.resource_id == pid)
                    .order_by(AuditLog.occurred_at)
                )
            )
            .scalars()
            .all()
        )
    actions = [r.action for r in rows]
    assert "catalog.product.create" in actions
    assert "catalog.product.block" in actions
    assert "catalog.product.unblock" in actions
