"""Auto-SEO engine.

When a product is uploaded (or updated), or a homepage banner is created,
this engine generates keyword-rich SEO metadata so the page ranks for
Bangladesh local-market search intent without any manual data entry.

Resolution chain in the public SEO API is:

    translation[locale]  →  override  →  builder default

This engine writes the *override* (en) + *translation* (bn) rows and
flags them ``auto_generated=True``. A manual admin edit through
``SeoAdminService.upsert_override`` flips the flag to ``False`` — after
that the engine never touches the row again (human curation wins).

Design rules honoured:
  * No placeholder/demo text — every string is derived from real product
    fields (name, brand, category, short description, primary image).
  * Soft-fail at the call site: a SEO failure must never block a product
    or banner write. Callers wrap invocations in try/except.
  * Idempotent: re-running over the same catalog only refreshes rows the
    engine itself owns.
"""
from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Product, ProductStatus
from app.modules.seo.keyword_bank import (
    expand_for_product as _kw_bank_product,
)
from app.modules.seo.models import SeoMetaOverride, SeoMetaTranslation
from app.modules.seo.repository import (
    SeoMetaOverrideRepository,
    SeoMetaTranslationRepository,
)

# Marketplace-wide intent keywords appended to every product page.
_BASE_KEYWORDS_EN = (
    "online shopping Bangladesh",
    "buy online BD",
    "cash on delivery",
    "best price Bangladesh",
    "Hypershop BD",
)
_BASE_KEYWORDS_BN = (
    "অনলাইন শপিং বাংলাদেশ",
    "ক্যাশ অন ডেলিভারি",
    "সেরা দাম বাংলাদেশ",
    "অনলাইন কেনাকাটা",
    "Hypershop BD",
)

_TITLE_MAX = 255
_DESC_MAX = 300
_KEYWORDS_MAX = 500
_ALT_MAX = 255


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _join_keywords(parts: Iterable[str], limit: int = _KEYWORDS_MAX) -> str:
    seen: list[str] = []
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        low = p.lower()
        if low not in {x.lower() for x in seen}:
            seen.append(p)
    out = ", ".join(seen)
    if len(out) <= limit:
        return out
    # Trim whole keywords until it fits.
    while seen and len(", ".join(seen)) > limit:
        seen.pop()
    return ", ".join(seen)


# ---------------------------------------------------------------------------
#  Pure builders (unit-testable, no DB)
# ---------------------------------------------------------------------------

def build_product_seo(
    *,
    name: str,
    brand: str | None,
    category: str | None,
    short_description: str | None,
) -> dict[str, Any]:
    """Return {en:{title,description,keywords}, bn:{...}} for a product."""
    name = (name or "").strip()
    brand = (brand or "").strip() or None
    category = (category or "").strip() or None
    short = (short_description or "").strip()

    # ---- EN ----
    en_title = _clip(
        f"{name} — Best Price in Bangladesh"
        + (f" | {brand}" if brand else "")
        + " | Hypershop BD",
        _TITLE_MAX,
    )

    en_desc_core = (
        f"Buy {name} online in Bangladesh at the best price on Hypershop BD."
    )
    if short:
        en_desc_core += f" {short}."
    en_desc = _clip(
        en_desc_core
        + " Cash on delivery, 100% genuine product & fast home delivery"
        + " across Bangladesh.",
        _DESC_MAX,
    )

    # Pull from the 21k+ BD purchase-intent bank — biases EN slots
    # with name × (intent/location/delivery/payment) combos. cap=28
    # leaves room for base set + brand combos under the 500-char limit.
    bank_en = _kw_bank_product(
        name=name, category_en=category, brand=brand, cap=28,
    )
    en_kw_parts = bank_en + [
        f"{name} price in Bangladesh",
        f"{name} BD",
        f"buy {name} online",
        f"{name} online Bangladesh",
    ]
    if brand:
        en_kw_parts += [brand, f"{brand} {name}"]
    if category:
        en_kw_parts += [category, f"{category} online Bangladesh",
                        f"best price {category} BD"]
    en_kw_parts += list(_BASE_KEYWORDS_EN)
    en_keywords = _join_keywords(en_kw_parts)

    # ---- BN ----
    bn_title = _clip(f"{name} কিনুন সেরা দামে বাংলাদেশে | Hypershop BD",
                     _TITLE_MAX)
    bn_desc = _clip(
        f"{name} অনলাইনে কিনুন Hypershop BD থেকে সেরা দামে।"
        + (f" {short}." if short else "")
        + " ক্যাশ অন ডেলিভারি, ১০০% আসল পণ্য ও সারা বাংলাদেশে দ্রুত হোম ডেলিভারি।",
        _DESC_MAX,
    )
    # Bank-driven BN slots — the bank's `expand_for_product` mixes
    # BN intent prefixes + Bangla location modifiers + question
    # templates so the row covers Bangla-script search shape too.
    bank_bn = [
        kw for kw in _kw_bank_product(
            name=name, category_bn=category, brand=brand, cap=20,
        )
        if any('ঀ' <= c <= '৿' for c in kw)  # contains Bangla script
    ]
    bn_kw_parts = bank_bn + [
        name,
        f"{name} দাম",
        f"{name} বাংলাদেশ",
        f"{name} অনলাইন",
    ]
    if brand:
        bn_kw_parts.append(brand)
    if category:
        bn_kw_parts.append(category)
    bn_kw_parts += list(_BASE_KEYWORDS_BN)
    bn_keywords = _join_keywords(bn_kw_parts)

    return {
        "en": {"title": en_title, "description": en_desc,
               "keywords": en_keywords},
        "bn": {"title": bn_title, "description": bn_desc,
               "keywords": bn_keywords},
    }


def build_banner_alt(title: str, subtitle: str | None) -> str:
    """Keyword-rich alt text for a homepage banner (image-search SEO)."""
    parts = [title.strip()]
    if subtitle and subtitle.strip():
        parts.append(subtitle.strip())
    parts.append("Hypershop BD — Online Shopping in Bangladesh")
    return _clip(" — ".join(parts), _ALT_MAX)


# ---------------------------------------------------------------------------
#  Service (DB writes)
# ---------------------------------------------------------------------------

class SeoAutoGenService:
    ENTITY = "product"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.overrides = SeoMetaOverrideRepository(session)
        self.translations = SeoMetaTranslationRepository(session)

    @staticmethod
    def _primary_image(product: Any) -> str | None:
        media = list(getattr(product, "media", None) or [])
        if not media:
            return None
        media.sort(key=lambda m: getattr(m, "position", 0) or 0)
        return getattr(media[0], "url", None)

    async def generate_for_product(
        self, product: Any, *, force: bool = False,
    ) -> bool:
        """Create/refresh auto SEO for one product.

        Returns True if a row was written, False if skipped because a
        manual (human-curated) override already exists.
        """
        key = product.id.hex if isinstance(product.id, UUID) else str(product.id)

        existing = await self.overrides.get(
            entity_type=self.ENTITY, entity_key=key,
        )
        if existing is not None and not existing.auto_generated and not force:
            return False  # human curation wins

        brand = getattr(getattr(product, "brand", None), "name", None)
        category = getattr(getattr(product, "category", None), "name", None)
        seo = build_product_seo(
            name=getattr(product, "name", "") or "",
            brand=brand,
            category=category,
            short_description=getattr(product, "short_description", None),
        )
        og_image = self._primary_image(product)

        await self.overrides.upsert(
            entity_type=self.ENTITY,
            entity_key=key,
            updated_by=None,
            title=seo["en"]["title"],
            meta_description=seo["en"]["description"],
            og_type="product",
            twitter_card="summary_large_image",
            og_image_url=og_image,
            extra_meta_json={"keywords": seo["en"]["keywords"]},
            auto_generated=True,
        )

        # bn translation — only refresh if absent or engine-owned.
        bn = await self.translations.get(
            entity_type=self.ENTITY, entity_key=key, locale="bn",
        )
        if bn is None or bn.auto_generated:
            await self.translations.upsert(
                entity_type=self.ENTITY,
                entity_key=key,
                locale="bn",
                updated_by=None,
                title=seo["bn"]["title"],
                meta_description=seo["bn"]["description"],
                keywords=seo["bn"]["keywords"],
                auto_generated=True,
            )
        return True

    async def backfill_products(self, *, limit: int = 500) -> dict[str, int]:
        """Generate auto SEO for active products that don't yet have ANY
        SEO override. Progressive + idempotent: each run only picks up
        products still missing an override, so repeated runs (or the
        nightly sweep) eventually cover the whole catalog without
        re-touching rows already done."""
        # entity_key stores the product UUID as 32-char hex (no dashes).
        covered = select(SeoMetaOverride.entity_key).where(
            SeoMetaOverride.entity_type == self.ENTITY,
        )
        product_key = func.replace(cast(Product.id, String), "-", "")
        rows = (
            await self.session.execute(
                select(Product)
                .where(
                    Product.status == ProductStatus.ACTIVE,
                    product_key.not_in(covered),
                )
                .order_by(Product.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

        written = 0
        skipped = 0
        for product in rows:
            if await self.generate_for_product(product):
                written += 1
            else:
                skipped += 1
        return {"scanned": len(rows), "written": written, "skipped": skipped}
