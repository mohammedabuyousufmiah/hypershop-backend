"""Integration smoke for Module 34 — SEO + Dynamic Content.

Most SEO endpoints are PUBLIC (no auth) so this exercises real
response shapes — the others are admin-write and just confirm 401/403.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.db.session import get_engine

pytestmark = pytest.mark.integration


# ---------------- migration ----------------
async def test_migration_0028_created_tables() -> None:
    expected = {
        "seo_meta_overrides",
        "homepage_banners",
        "blog_posts",
        "seo_url_redirects",
    }
    engine = get_engine()
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select tablename from pg_tables "
                    "where schemaname='public' AND tablename = ANY(:names)",
                ),
                {"names": list(expected)},
            )
        ).all()
    found = {r[0] for r in rows}
    assert found == expected, f"missing: {expected - found}"


# ---------------- root-mounted endpoints ----------------
async def test_robots_txt_is_publicly_served(api_client: AsyncClient) -> None:
    resp = await api_client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "User-agent: *" in body
    assert "Sitemap:" in body
    assert "Disallow: /api/" in body
    assert "Disallow: /admin/" in body


async def test_sitemap_xml_is_a_sitemap_index(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "xml" in resp.headers["content-type"]
    body = resp.text
    assert body.startswith("<?xml")
    # /sitemap.xml is now the index, not a flat urlset.
    assert "<sitemapindex" in body
    # The static section always has URLs, so its child is always listed.
    assert "/sitemap-static-0.xml" in body


async def test_child_sitemap_is_a_urlset(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/sitemap-static-0.xml")
    assert resp.status_code == 200
    assert "xml" in resp.headers["content-type"]
    body = resp.text
    assert body.startswith("<?xml")
    assert "<urlset" in body
    # Static homepage URL always present.
    assert "<loc>" in body


async def test_child_sitemap_rejects_unknown_kind(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/sitemap-bogus-0.xml")
    assert resp.status_code == 422


async def test_unknown_redirect_path_returns_404(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/r/some/random/path", follow_redirects=False)
    assert resp.status_code == 404


# ---------------- public JSON endpoints ----------------
# NOTE: JSON responses pass through the standard envelope middleware
# ({success, message, data, meta}) — assertions unwrap ``data``. The
# organization JSON-LD @type is "OnlineStore" in the marketplace build
# (the pharmacy-era "Pharmacy" type retired with the 2026-05-17 purge).
async def test_site_config_returns_organization_jsonld(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/seo/site-config")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["site_name"]
    assert body["site_url"]
    org = body["organization_jsonld"]
    assert org["@type"] == "OnlineStore"
    assert org["@context"] == "https://schema.org"
    assert org["name"] == body["site_name"]
    assert org["address"]["addressCountry"] in ("BD", "US", "IN", "PK")


async def test_home_meta_bundle_has_seo_essentials(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/seo/meta/home")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["title"]
    assert body["meta_description"]
    assert body["canonical_url"].startswith("http")
    assert body["og_type"] == "website"
    assert body["twitter_card"] in ("summary", "summary_large_image")
    # JSON-LD: organization + breadcrumb minimum.
    types = {j.get("@type") for j in body["jsonld"]}
    assert "OnlineStore" in types
    assert "BreadcrumbList" in types


async def test_banners_list_returns_empty_array_when_none(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/seo/banners")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert "items" in body
    assert isinstance(body["items"], list)


async def test_blog_list_returns_empty_array_when_none(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/seo/blog")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert "items" in body
    assert isinstance(body["items"], list)


async def test_blog_unknown_slug_returns_404(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/seo/blog/does-not-exist")
    assert resp.status_code == 404


async def test_unknown_product_meta_returns_404(
    api_client: AsyncClient,
) -> None:
    # Random valid UUID — should resolve to "entity not found".
    resp = await api_client.get(
        "/api/v1/seo/meta/product/00000000-0000-0000-0000-000000000000",
    )
    assert resp.status_code == 404


# ---------------- admin endpoints (auth-gated) ----------------
async def test_admin_overrides_requires_perm(api_client: AsyncClient) -> None:
    resp = await api_client.put(
        "/api/v1/admin/seo/overrides",
        json={
            "entity_type": "static_page",
            "entity_key": "home",
            "title": "Custom",
        },
    )
    assert resp.status_code in (401, 403)


async def test_admin_blog_create_requires_perm(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/admin/seo/blog",
        json={
            "slug": "smoke-test",
            "title": "smoke",
            "body_markdown": "x",
        },
    )
    assert resp.status_code in (401, 403)


async def test_admin_redirects_create_requires_perm(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/seo/redirects",
        json={"from_path": "/old", "to_path": "/new"},
    )
    assert resp.status_code in (401, 403)
