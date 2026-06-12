"""Internal link graph optimizer — auto-suggest 5 related pages per URL.

Strategy:
  - Category-sibling: same parent category, sorted by traffic
  - Brand-sibling: same brand, different category
  - Related-howto: HowTo / buying-guide pages mentioning the entity
  - Upsell: higher-tier products in same category
  - Comparison: "X vs Y" pages where this URL is X or Y
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkSuggestion:
    source_url: str
    target_url: str
    anchor_text: str
    link_type: str  # related | upsell | category_sibling | brand_sibling | how_to | comparison
    relevance_score: float


def suggest_for_product(
    *,
    product_url: str,
    product_name: str,
    category_slug: str,
    brand_slug: str | None,
    siblings_in_category: list[tuple[str, str]],  # [(url, name)]
    same_brand_other_cat: list[tuple[str, str]],
    related_guides: list[tuple[str, str]],
    upsells: list[tuple[str, str]],
    max_links: int = 5,
) -> list[LinkSuggestion]:
    """Return up to ``max_links`` link suggestions for a product page."""
    out: list[LinkSuggestion] = []

    # 1 upsell (highest converting)
    if upsells:
        u_url, u_name = upsells[0]
        out.append(LinkSuggestion(
            source_url=product_url,
            target_url=u_url,
            anchor_text=f"Upgrade to {u_name}",
            link_type="upsell",
            relevance_score=0.95,
        ))

    # 2 category siblings
    for u_url, u_name in siblings_in_category[:2]:
        out.append(LinkSuggestion(
            source_url=product_url,
            target_url=u_url,
            anchor_text=u_name,
            link_type="category_sibling",
            relevance_score=0.85,
        ))

    # 1 brand sibling (different category)
    if brand_slug and same_brand_other_cat:
        u_url, u_name = same_brand_other_cat[0]
        out.append(LinkSuggestion(
            source_url=product_url,
            target_url=u_url,
            anchor_text=f"More from {brand_slug.title()}",
            link_type="brand_sibling",
            relevance_score=0.7,
        ))

    # 1 related how-to
    if related_guides:
        u_url, u_name = related_guides[0]
        out.append(LinkSuggestion(
            source_url=product_url,
            target_url=u_url,
            anchor_text=u_name,
            link_type="how_to",
            relevance_score=0.6,
        ))

    return out[:max_links]


def suggest_for_category(
    *,
    category_url: str,
    category_name: str,
    top_products: list[tuple[str, str]],
    related_categories: list[tuple[str, str]],
    buying_guides: list[tuple[str, str]],
    max_links: int = 8,
) -> list[LinkSuggestion]:
    """Category-page link suggestions."""
    out: list[LinkSuggestion] = []

    # 3 top products
    for u_url, u_name in top_products[:3]:
        out.append(LinkSuggestion(
            source_url=category_url,
            target_url=u_url,
            anchor_text=u_name,
            link_type="related",
            relevance_score=0.9,
        ))

    # 3 related categories
    for u_url, u_name in related_categories[:3]:
        out.append(LinkSuggestion(
            source_url=category_url,
            target_url=u_url,
            anchor_text=f"Browse {u_name}",
            link_type="category_sibling",
            relevance_score=0.75,
        ))

    # 2 buying guides
    for u_url, u_name in buying_guides[:2]:
        out.append(LinkSuggestion(
            source_url=category_url,
            target_url=u_url,
            anchor_text=u_name,
            link_type="how_to",
            relevance_score=0.65,
        ))

    return out[:max_links]
