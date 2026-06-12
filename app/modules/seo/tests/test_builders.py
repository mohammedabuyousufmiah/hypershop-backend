"""Pure-function tests for the SEO bundle builders."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.seo import builders
from app.modules.seo.builders import (
    SeoBundle,
    SiteContext,
    absolute_url,
    apply_override,
    breadcrumb_jsonld,
    organization_jsonld,
    truncate_description,
)
from app.modules.seo.state import (
    AVAILABILITY_IN_STOCK,
    AVAILABILITY_OUT_OF_STOCK,
    JSONLD_TYPE_BREADCRUMB_LIST,
    JSONLD_TYPE_ORGANIZATION,
    JSONLD_TYPE_PRODUCT,
    OgType,
)


CTX = SiteContext(
    site_name="Hypershop",
    site_url="https://hypershop.example",
    default_og_image="/static/og.svg",
    pharmacy_phone="+8801700000000",
)


# ---------------- Helpers ----------------
def test_absolute_url_passes_through_full_urls():
    assert absolute_url(ctx=CTX, path="https://cdn.example/x.jpg") == \
        "https://cdn.example/x.jpg"


def test_absolute_url_resolves_relative():
    assert absolute_url(ctx=CTX, path="/foo/bar") == \
        "https://hypershop.example/foo/bar"


def test_absolute_url_handles_missing_leading_slash():
    assert absolute_url(ctx=CTX, path="foo") == \
        "https://hypershop.example/foo"


def test_truncate_description_caps_length():
    long = "Lorem ipsum dolor sit amet, " * 30
    out = truncate_description(long, max_len=160)
    assert len(out) <= 161  # +1 for the ellipsis char
    assert out.endswith("…")


def test_truncate_description_short_text_unchanged():
    assert truncate_description("hello") == "hello"


def test_truncate_description_collapses_whitespace():
    assert truncate_description("hello\n\n  world") == "hello world"


# ---------------- Schema blocks ----------------
def test_organization_jsonld_includes_pharmacy_type():
    block = organization_jsonld(CTX)
    assert block["@type"] == JSONLD_TYPE_ORGANIZATION
    assert block["name"] == "Hypershop"
    assert block["url"] == "https://hypershop.example"
    assert block["telephone"] == "+8801700000000"
    assert block["address"]["addressCountry"] == "BD"


def test_breadcrumb_jsonld_uses_position_index():
    block = breadcrumb_jsonld(
        CTX, [("Home", "/"), ("Products", "/products"), ("Foo", "/p/foo")],
    )
    assert block["@type"] == JSONLD_TYPE_BREADCRUMB_LIST
    items = block["itemListElement"]
    assert len(items) == 3
    assert items[0]["position"] == 1
    assert items[2]["position"] == 3
    assert items[0]["item"] == "https://hypershop.example/"


# ---------------- Per-entity ----------------
def test_build_home_meta_emits_org_and_breadcrumb():
    bundle = builders.build_home_meta(ctx=CTX)
    assert bundle.title == "Hypershop"
    assert bundle.canonical_url == "https://hypershop.example/"
    assert bundle.og_type == OgType.WEBSITE.value
    assert any(j["@type"] == JSONLD_TYPE_ORGANIZATION for j in bundle.jsonld)
    assert any(j["@type"] == JSONLD_TYPE_BREADCRUMB_LIST for j in bundle.jsonld)


def test_build_product_meta_emits_product_jsonld_with_offer():
    product = SimpleNamespace(
        id=uuid4(),
        name="Paracetamol 500mg",
        slug="paracetamol-500mg",
        description="For pain and fever relief.",
        price=Decimal("12.50"),
        currency="BDT",
        sku="PARA-500",
        in_stock=True,
        primary_image_url="/media/p1.jpg",
        rating=Decimal("4.5"),
        review_count=23,
        brand=SimpleNamespace(name="Square"),
    )
    bundle = builders.build_product_meta(ctx=CTX, product=product)
    assert "Paracetamol 500mg" in bundle.title
    assert bundle.og_type == OgType.PRODUCT.value
    pjs = [j for j in bundle.jsonld if j["@type"] == JSONLD_TYPE_PRODUCT]
    assert len(pjs) == 1
    schema = pjs[0]
    assert schema["sku"] == "PARA-500"
    assert schema["brand"]["name"] == "Square"
    assert schema["offers"]["price"] == "12.50"
    assert schema["offers"]["availability"] == AVAILABILITY_IN_STOCK
    assert schema["aggregateRating"]["ratingValue"] == 4.5


def test_build_product_meta_out_of_stock_availability():
    product = SimpleNamespace(
        id=uuid4(), name="X", slug="x", description="x",
        price=Decimal("1.00"), currency="BDT", sku="X-1",
        in_stock=False, primary_image_url=None,
        rating=None, review_count=None, brand=None,
    )
    bundle = builders.build_product_meta(ctx=CTX, product=product)
    schema = next(j for j in bundle.jsonld if j["@type"] == JSONLD_TYPE_PRODUCT)
    assert schema["offers"]["availability"] == AVAILABILITY_OUT_OF_STOCK


def test_build_product_meta_no_price_means_no_offer_block():
    product = SimpleNamespace(
        id=uuid4(), name="X", slug="x", description="x",
        price=None, currency="BDT", sku="X-1", in_stock=True,
        primary_image_url=None, rating=None, review_count=None, brand=None,
    )
    bundle = builders.build_product_meta(ctx=CTX, product=product)
    schema = next(j for j in bundle.jsonld if j["@type"] == JSONLD_TYPE_PRODUCT)
    assert "offers" not in schema


def test_build_blog_post_meta_emits_article_og_type():
    post = SimpleNamespace(
        slug="how-to-take-paracetamol",
        title="How to take paracetamol safely",
        excerpt="A short guide.",
        cover_image_url="/media/blog/p.jpg",
        author_name="Dr. Rahman",
        published_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
    )
    bundle = builders.build_blog_post_meta(ctx=CTX, post=post)
    assert bundle.og_type == OgType.ARTICLE.value
    assert "How to take paracetamol safely" in bundle.title
    blog = next(j for j in bundle.jsonld if j["@type"] == "BlogPosting")
    assert blog["author"]["name"] == "Dr. Rahman"
    assert blog["datePublished"].startswith("2026-05-04")


def test_build_static_page_uses_breadcrumb_default():
    bundle = builders.build_static_page_meta(
        ctx=CTX, slug="about", title="About Us",
        description="Who we are.",
    )
    assert bundle.canonical_url == "https://hypershop.example/about"


# ---------------- Override merger ----------------
def test_apply_override_replaces_named_fields():
    bundle = builders.build_home_meta(ctx=CTX)
    base_title = bundle.title
    override = SimpleNamespace(
        title="Custom Home Title",
        meta_description=None,
        canonical_url=None,
        og_image_url=None,
        og_type=None,
        twitter_card=None,
        robots_directives=None,
        extra_meta_json={},
        extra_jsonld_json=[],
    )
    out = apply_override(bundle, override=override)
    assert out.title == "Custom Home Title"
    assert out.meta_title == "Custom Home Title"
    assert out.title != base_title


def test_apply_override_merges_extra_meta_and_jsonld():
    bundle = builders.build_home_meta(ctx=CTX)
    initial_count = len(bundle.jsonld)
    override = SimpleNamespace(
        title=None, meta_description=None, canonical_url=None,
        og_image_url=None, og_type=None, twitter_card=None,
        robots_directives="noindex",
        extra_meta_json={"keywords": "pharmacy bd"},
        extra_jsonld_json=[{"@type": "FAQPage", "mainEntity": []}],
    )
    out = apply_override(bundle, override=override)
    assert out.robots == "noindex"
    assert out.extra_meta == {"keywords": "pharmacy bd"}
    assert len(out.jsonld) == initial_count + 1


def test_apply_override_with_none_returns_bundle_unchanged():
    bundle = builders.build_home_meta(ctx=CTX)
    out = apply_override(bundle, override=None)
    assert out is bundle


# ---------------- Sitemap renderer ----------------
def test_sitemap_renderer_handles_lastmod_and_xml_escape():
    from datetime import date
    from app.modules.seo.service import _render_sitemap, _xml_escape

    xml = _render_sitemap([
        ("https://hypershop.example/", None),
        ("https://hypershop.example/x?q=a&b=c", date(2026, 5, 4)),
    ])
    assert "<?xml" in xml
    assert "<urlset" in xml
    assert "<lastmod>2026-05-04</lastmod>" in xml
    # & must be escaped to &amp;
    assert "q=a&amp;b=c" in xml
    # No raw '&' in URLs.
    assert "q=a&b=c" not in xml


def test_xml_escape_handles_all_special_chars():
    from app.modules.seo.service import _xml_escape
    assert _xml_escape("<>&\"'") == "&lt;&gt;&amp;&quot;&apos;"


# ---------------- FAQPage JSON-LD ----------------
def test_faqpage_jsonld_structure():
    blk = builders.faqpage_jsonld(
        [("Is it genuine?", "Yes, 100% authentic."), ("Delivery?", "24-48h.")],
    )
    assert blk["@type"] == "FAQPage"
    assert len(blk["mainEntity"]) == 2
    q0 = blk["mainEntity"][0]
    assert q0["@type"] == "Question"
    assert q0["name"] == "Is it genuine?"
    assert q0["acceptedAnswer"]["@type"] == "Answer"
    assert q0["acceptedAnswer"]["text"] == "Yes, 100% authentic."


def _faq_product():
    return SimpleNamespace(
        id=uuid4(),
        name="Test Product",
        slug="test-product",
        description="A product.",
        price=Decimal("10.00"),
        currency="BDT",
        sku="T-1",
        in_stock=True,
        primary_image_url=None,
        rating=None,
        review_count=None,
        brand=None,
    )


def test_build_product_meta_appends_faqpage_when_faqs_present():
    bundle = builders.build_product_meta(
        ctx=CTX, product=_faq_product(), faqs=[("Q1", "A1")],
    )
    types = [j["@type"] for j in bundle.jsonld]
    assert "FAQPage" in types


def test_build_product_meta_omits_faqpage_without_faqs():
    bundle = builders.build_product_meta(ctx=CTX, product=_faq_product())
    types = [j["@type"] for j in bundle.jsonld]
    assert "FAQPage" not in types


def test_build_category_meta_appends_faqpage_when_faqs_present():
    cat = SimpleNamespace(id=uuid4(), name="Electronics", slug="electronics", description=None)
    bundle = builders.build_category_meta(ctx=CTX, category=cat, faqs=[("Q1", "A1")])
    types = [j["@type"] for j in bundle.jsonld]
    assert "FAQPage" in types


def test_build_category_meta_omits_faqpage_without_faqs():
    cat = SimpleNamespace(id=uuid4(), name="Electronics", slug="electronics", description=None)
    bundle = builders.build_category_meta(ctx=CTX, category=cat)
    types = [j["@type"] for j in bundle.jsonld]
    assert "FAQPage" not in types
