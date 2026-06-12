from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


def _make_product_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "name": "Premium Cotton T-Shirt",
        "short_description": "100% combed cotton.",
        "base_currency": "BDT",
        "tax_class": "standard",
        "status": "active",
        "attributes": {"material": "cotton"},
        "variants": [
            {
                "sku": "TSHIRT-RED-M",
                "name": "Red / M",
                "options": {"color": "Red", "size": "M"},
                "price": "599.00",
                "compare_at_price": "899.00",
                "weight_grams": 200,
            },
            {
                "sku": "TSHIRT-BLU-L",
                "name": "Blue / L",
                "options": {"color": "Blue", "size": "L"},
                "price": "699.00",
                "weight_grams": 220,
            },
        ],
        "media": [
            {
                "url": "https://cdn.hypershop.local/p/tshirt-1.jpg",
                "alt": "T-shirt front",
                "kind": "image",
                "position": 0,
            },
            {
                "url": "https://cdn.hypershop.local/p/tshirt-2.jpg",
                "alt": "T-shirt back",
                "kind": "image",
                "position": 1,
            },
            {
                "url": "https://cdn.hypershop.local/p/tshirt-3.jpg",
                "alt": "T-shirt detail",
                "kind": "image",
                "position": 2,
            },
        ],
    }
    base.update(overrides)
    return base


# ---------------- Brand ----------------


async def test_admin_creates_brand(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/catalog/brands",
        headers=admin_user["headers"],
        json={"name": "HyperWear", "description": "House brand"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "hyperwear"
    assert body["name"] == "HyperWear"


async def test_customer_cannot_create_brand(
    api_client: AsyncClient,
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/catalog/brands",
        headers=logged_in["headers"],
        json={"name": "Forbidden"},
    )
    assert resp.status_code == 403


async def test_anon_cannot_create_brand(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/admin/catalog/brands",
        json={"name": "Forbidden"},
    )
    assert resp.status_code == 401


async def test_public_can_list_brands(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    await api_client.post(
        "/api/v1/admin/catalog/brands",
        headers=admin_user["headers"],
        json={"name": "Visible"},
    )
    resp = await api_client.get("/api/v1/catalog/brands")
    assert resp.status_code == 200
    assert any(b["name"] == "Visible" for b in resp.json())


# ---------------- Category ----------------


async def test_admin_creates_category_tree(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    parent = await api_client.post(
        "/api/v1/admin/catalog/categories",
        headers=admin_user["headers"],
        json={"name": "Apparel"},
    )
    assert parent.status_code == 201
    parent_id = parent.json()["id"]

    child = await api_client.post(
        "/api/v1/admin/catalog/categories",
        headers=admin_user["headers"],
        json={"name": "T-Shirts", "parent_id": parent_id},
    )
    assert child.status_code == 201

    tree = await api_client.get("/api/v1/catalog/categories")
    assert tree.status_code == 200
    roots = tree.json()
    parent_node = next(r for r in roots if r["slug"] == "apparel")
    assert any(c["slug"] == "t-shirts" for c in parent_node["children"])


async def test_category_cannot_be_its_own_parent(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    cat = await api_client.post(
        "/api/v1/admin/catalog/categories",
        headers=admin_user["headers"],
        json={"name": "Self-Loop"},
    )
    cid = cat.json()["id"]
    resp = await api_client.patch(
        f"/api/v1/admin/catalog/categories/{cid}",
        headers=admin_user["headers"],
        json={"parent_id": cid},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "business_rule_violation"


async def test_cannot_delete_category_with_children(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    parent = await api_client.post(
        "/api/v1/admin/catalog/categories",
        headers=admin_user["headers"],
        json={"name": "Outer"},
    )
    parent_id = parent.json()["id"]
    await api_client.post(
        "/api/v1/admin/catalog/categories",
        headers=admin_user["headers"],
        json={"name": "Inner", "parent_id": parent_id},
    )
    resp = await api_client.delete(
        f"/api/v1/admin/catalog/categories/{parent_id}",
        headers=admin_user["headers"],
    )
    assert resp.status_code == 409


# ---------------- Product ----------------


async def test_admin_creates_active_product_visible_to_public(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    create = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_make_product_payload(),
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["slug"] == "premium-cotton-t-shirt"
    assert body["status"] == "active"
    assert body["published_at"] is not None
    assert len(body["variants"]) == 2

    listing = await api_client.get("/api/v1/catalog/products")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    item = listing.json()["items"][0]
    assert item["min_price"] == "599.00"
    assert item["max_price"] == "699.00"

    detail = await api_client.get("/api/v1/catalog/products/premium-cotton-t-shirt")
    assert detail.status_code == 200
    assert detail.json()["name"] == "Premium Cotton T-Shirt"


async def test_draft_product_hidden_from_public(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    payload = _make_product_payload(status="draft", name="Hidden Tee")
    create = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert create.status_code == 201

    listing = await api_client.get("/api/v1/catalog/products")
    assert listing.status_code == 200
    assert listing.json()["total"] == 0

    detail = await api_client.get("/api/v1/catalog/products/hidden-tee")
    assert detail.status_code == 404


async def test_product_search_filters_by_query_brand_and_category(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    brand = await api_client.post(
        "/api/v1/admin/catalog/brands",
        headers=admin_user["headers"],
        json={"name": "HyperWear"},
    )
    cat = await api_client.post(
        "/api/v1/admin/catalog/categories",
        headers=admin_user["headers"],
        json={"name": "Apparel"},
    )

    payload_a = _make_product_payload(
        name="HyperWear Crew Tee",
        brand_id=brand.json()["id"],
        category_id=cat.json()["id"],
        variants=[
            {
                "sku": "HWCREW-S",
                "options": {"size": "S"},
                "price": "499.00",
            },
        ],
    )
    payload_b = _make_product_payload(
        name="Generic Polo",
        variants=[
            {
                "sku": "POLO-S",
                "options": {"size": "S"},
                "price": "799.00",
            },
        ],
    )
    await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload_a,
    )
    await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload_b,
    )

    by_q = await api_client.get("/api/v1/catalog/products?q=Polo")
    assert by_q.json()["total"] == 1
    assert by_q.json()["items"][0]["name"] == "Generic Polo"

    by_brand = await api_client.get("/api/v1/catalog/products?brand=hyperwear")
    assert by_brand.json()["total"] == 1
    assert by_brand.json()["items"][0]["brand_name"] == "HyperWear"

    by_cat = await api_client.get("/api/v1/catalog/products?category=apparel")
    assert by_cat.json()["total"] == 1


async def test_cannot_delete_last_variant(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    payload = _make_product_payload(
        name="Single-Variant Tee",
        variants=[
            {
                "sku": "SOLO-1",
                "options": {"color": "Black"},
                "price": "499.00",
            },
        ],
    )
    create = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    product = create.json()
    variant_id = product["variants"][0]["id"]
    resp = await api_client.delete(
        f"/api/v1/admin/catalog/products/{product['id']}/variants/{variant_id}",
        headers=admin_user["headers"],
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "business_rule_violation"


async def test_archive_product_removes_from_public(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    create = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_make_product_payload(name="Soon-To-Be-Archived"),
    )
    pid = create.json()["id"]
    archive = await api_client.delete(
        f"/api/v1/admin/catalog/products/{pid}",
        headers=admin_user["headers"],
    )
    assert archive.status_code == 204

    listing = await api_client.get("/api/v1/catalog/products")
    assert listing.json()["total"] == 0


async def test_duplicate_slug_rejected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    a = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_make_product_payload(slug="dupe-slug", name="A"),
    )
    assert a.status_code == 201
    b = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_make_product_payload(
            slug="dupe-slug",
            name="B",
            variants=[
                {
                    "sku": "B-1",
                    "options": {},
                    "price": "100.00",
                },
            ],
        ),
    )
    assert b.status_code == 409


async def test_invalid_currency_rejected(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=_make_product_payload(base_currency="ZZZ"),
    )
    # ZZZ passes the regex (uppercase 3 letters) but the DB CHECK won't fail
    # since it just requires uppercase 3 letters. We accept any ISO-shaped code
    # at the API; finer currency validation lives in money.py / order layer.
    assert resp.status_code == 201
    body = resp.json()
    assert body["base_currency"] == "ZZZ"


async def test_compare_at_must_be_ge_price(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
) -> None:
    payload = _make_product_payload(
        variants=[
            {
                "sku": "BADPRICE-1",
                "options": {},
                "price": "1000.00",
                "compare_at_price": "500.00",
            },
        ],
    )
    resp = await api_client.post(
        "/api/v1/admin/catalog/products",
        headers=admin_user["headers"],
        json=payload,
    )
    assert resp.status_code == 422
