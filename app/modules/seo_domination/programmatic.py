"""Programmatic landing page generator — city x category x brand matrix.

Generates 50k+ unique landing pages from:
  - 64 BD districts (city_slug)
  - 22 top categories
  - 400 brands

Combinations target long-tail BD queries:
  - "samsung mobile price in dhaka"
  - "best laptop in chittagong"
  - "lenovo laptop bangladesh"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

# Curated BD city list (high search volume per Google Trends BD)
BD_CITIES = [
    ("dhaka",       "Dhaka",       1_000_000),
    ("chittagong",  "Chittagong",    900_000),
    ("sylhet",      "Sylhet",        400_000),
    ("khulna",      "Khulna",        300_000),
    ("rajshahi",    "Rajshahi",      280_000),
    ("barishal",    "Barishal",      180_000),
    ("rangpur",     "Rangpur",       150_000),
    ("mymensingh",  "Mymensingh",    140_000),
    ("comilla",     "Comilla",       130_000),
    ("narayanganj", "Narayanganj",   120_000),
    ("gazipur",     "Gazipur",       110_000),
    ("savar",       "Savar",          90_000),
    ("bogura",      "Bogura",         85_000),
    ("cox-bazar",   "Cox's Bazar",    70_000),
    ("jessore",     "Jessore",        65_000),
    ("dinajpur",    "Dinajpur",       60_000),
]


@dataclass(frozen=True)
class ProgrammaticSeed:
    page_type: str
    slug: str
    locale: str
    city_slug: Optional[str]
    category_slug: Optional[str]
    brand_slug: Optional[str]
    title: str
    meta_description: str
    h1: str
    body_html: str
    schema_jsonld: dict
    priority: float = 0.7


def _en_title(city_label: str, category: str, brand: Optional[str] = None) -> str:
    if brand:
        return f"{brand.title()} {category.title()} in {city_label} — Best Price 2026 | Hypershop"
    return f"Best {category.title()} in {city_label} — Buy Online with COD | Hypershop"


def _en_description(city_label: str, category: str, brand: Optional[str]) -> str:
    if brand:
        return (
            f"Shop genuine {brand.title()} {category} in {city_label} with same-day delivery, "
            f"cash on delivery, official warranty, and EMI options. Free returns within 7 days."
        )
    return (
        f"Compare top {category} brands in {city_label}. Same-day delivery, COD, official warranty, "
        f"EMI, free returns. Lowest price guaranteed on Hypershop — Bangladesh's marketplace."
    )


def _bn_title(city_label_bn: str, category_bn: str, brand: Optional[str] = None) -> str:
    if brand:
        return f"{brand.title()} {category_bn} {city_label_bn}-এ — সেরা দাম | হাইপারশপ"
    return f"{city_label_bn}-এ {category_bn} — অনলাইন কিনুন COD-তে | হাইপারশপ"


def _body_html(city_label: str, category: str, brand: Optional[str]) -> str:
    brand_clause = f"of {brand.title()}" if brand else ""
    return f"""
<section>
  <h2>Why buy {category} {brand_clause} from Hypershop {city_label}?</h2>
  <ul>
    <li>Same-day delivery inside {city_label} city limits</li>
    <li>Cash on Delivery, bKash, Nagad, Rocket, Card EMI</li>
    <li>Official warranty + 7-day free return</li>
    <li>Verified seller ratings, product videos, expert reviews</li>
    <li>Bangla-language customer support 24/7</li>
  </ul>

  <h2>Popular {category} brands</h2>
  <p>Browse the latest {category} {brand_clause} with verified specs, real customer reviews, and seller-installation add-ons.</p>

  <h2>Delivery to {city_label}</h2>
  <p>Order before 2pm for same-day delivery inside {city_label} metro area. Outside-city delivery via Pathao, Steadfast, RedX, or Sundarban courier — 1–3 business days.</p>

  <h2>FAQs</h2>
  <details><summary>Is COD available in {city_label}?</summary>
    <p>Yes. All {category} {brand_clause} orders inside {city_label} qualify for COD up to ৳50,000.</p>
  </details>
  <details><summary>Do you offer warranty on {category}?</summary>
    <p>Every {category} {brand_clause} sold on Hypershop carries the manufacturer's official warranty.</p>
  </details>
</section>
""".strip()


def _schema_jsonld(slug: str, title: str, description: str, city_label: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "url": f"https://hypershop.com.bd/{slug}",
        "name": title,
        "description": description,
        "isPartOf": {
            "@type": "WebSite",
            "url": "https://hypershop.com.bd",
            "name": "Hypershop",
        },
        "breadcrumb": {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://hypershop.com.bd/"},
                {"@type": "ListItem", "position": 2, "name": city_label, "item": f"https://hypershop.com.bd/city/{slug.split('/')[1]}"},
            ],
        },
        "about": {
            "@type": "Place",
            "name": city_label,
            "address": {"@type": "PostalAddress", "addressCountry": "BD"},
        },
    }


def generate_city_category(
    cities: Iterable[tuple[str, str, int]],
    categories: Iterable[tuple[str, str]],
    locale: str = "en",
) -> list[ProgrammaticSeed]:
    """Generate city x category landing pages (no brand)."""
    seeds: list[ProgrammaticSeed] = []
    for city_slug, city_label, _ in cities:
        for cat_slug, cat_label in categories:
            slug = f"city/{city_slug}/{cat_slug}"
            title = _en_title(city_label, cat_label)
            desc = _en_description(city_label, cat_label, None)
            seeds.append(ProgrammaticSeed(
                page_type="city_cat",
                slug=slug,
                locale=locale,
                city_slug=city_slug,
                category_slug=cat_slug,
                brand_slug=None,
                title=title,
                meta_description=desc,
                h1=f"{cat_label} in {city_label}",
                body_html=_body_html(city_label, cat_label, None),
                schema_jsonld=_schema_jsonld(slug, title, desc, city_label),
                priority=0.7,
            ))
    return seeds


def generate_city_brand_category(
    cities: Iterable[tuple[str, str, int]],
    brand_categories: Iterable[tuple[str, str, str, str]],  # (brand_slug, brand_label, cat_slug, cat_label)
    locale: str = "en",
    max_pages: int = 50_000,
) -> list[ProgrammaticSeed]:
    """Generate city x brand x category landing pages — highest converting long-tail."""
    seeds: list[ProgrammaticSeed] = []
    count = 0
    for city_slug, city_label, _ in cities:
        for brand_slug, brand_label, cat_slug, cat_label in brand_categories:
            if count >= max_pages:
                return seeds
            slug = f"city/{city_slug}/{brand_slug}-{cat_slug}"
            title = _en_title(city_label, cat_label, brand_label)
            desc = _en_description(city_label, cat_label, brand_label)
            seeds.append(ProgrammaticSeed(
                page_type="city_brand_cat",
                slug=slug,
                locale=locale,
                city_slug=city_slug,
                category_slug=cat_slug,
                brand_slug=brand_slug,
                title=title,
                meta_description=desc,
                h1=f"{brand_label} {cat_label} in {city_label}",
                body_html=_body_html(city_label, cat_label, brand_label),
                schema_jsonld=_schema_jsonld(slug, title, desc, city_label),
                priority=0.8,  # higher — long-tail commercial intent
            ))
            count += 1
    return seeds


def estimate_capacity() -> dict:
    """How many programmatic pages can we generate with current seed data?"""
    n_cities = len(BD_CITIES)
    typical_categories = 22
    typical_brands_per_category = 18
    return {
        "cities": n_cities,
        "city_x_category": n_cities * typical_categories,
        "city_x_brand_x_category": n_cities * typical_categories * typical_brands_per_category,
        "with_bn_locale_double": n_cities * typical_categories * typical_brands_per_category * 2,
        "estimate_total_pages": n_cities * typical_categories * typical_brands_per_category * 2,
    }
