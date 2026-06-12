"""Re-tag every product's primary loremflickr image with keywords
derived from the product name + brand, so each PDP shows a sensibly
themed photo instead of a generic (and sometimes wildly wrong) match.

Why this exists
---------------
``seed_catalog_demo.py`` seeded most products with broad category
tags like ``electronics,gadget,phone``. Loremflickr's keyword search
then returns whatever its corpus has — so the **Apple Watch SE**
product was getting a stock photo of a Nokia flip-phone, the **Tangail
silk saree** was getting a random fabric swatch, etc.

Fix: deterministic keyword resolver based on product name + brand
name. Each product gets a stable seed (``lock=hash(slug)``) so the
photo doesn't flicker across page reloads.

Run: ``python -m scripts.fix_product_image_tags``
Idempotent — overwrites the existing URL with a new one derived from
the same slug, so re-running keeps the same photo per product.
"""
from __future__ import annotations

import asyncio
import os
import re

# Manual .env load (bypasses Git Bash MSYS path mangling on Windows).
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from sqlalchemy import text  # noqa: E402

from app.core.db.session import get_sessionmaker  # noqa: E402


# ---------------------------------------------------------------- tag rules

# Keyword → tag mapping. First match wins (specific before generic).
# Tags are loremflickr search terms; multi-word terms use a comma so
# loremflickr ORs them.
RULES: list[tuple[str, str]] = [
    # ── Wearables / smartwatches ─────────────────────────────────
    (r"\bapple\s*watch\b",          "apple-watch,smartwatch,wearable"),
    (r"\bgalaxy\s*watch\b",         "samsung-galaxy-watch,smartwatch"),
    (r"\bsmart\s*watch\b|noise\s+watch", "smartwatch,fitness-watch,wearable"),
    (r"\bfitness\s*band\b|fitbit",  "fitness-tracker,wristband"),

    # ── Phones ───────────────────────────────────────────────────
    (r"\biphone\b",                 "iphone,apple-phone,smartphone"),
    (r"\bsamsung\s*galaxy\s*s\b|galaxy s\d",        "samsung-galaxy-s,smartphone"),
    (r"\bgalaxy\s*a\d|galaxy a\b",                  "samsung-galaxy-a,smartphone"),
    (r"\bredmi|xiaomi.*note|redmi-note",            "xiaomi-redmi,smartphone"),
    (r"\boppo|vivo|realme\b",                       "android-phone,smartphone"),
    (r"\bsmartphone|mobile phone\b",                "smartphone,modern-phone"),

    # ── Laptops / tablets ────────────────────────────────────────
    (r"\bmacbook\b",                "macbook,apple-laptop"),
    (r"\bipad\b",                   "ipad,apple-tablet,tablet"),
    (r"\blaptop|notebook\b",        "laptop,modern-laptop"),
    (r"\btablet\b",                 "tablet,android-tablet"),

    # ── Audio ────────────────────────────────────────────────────
    (r"\bairpods\b",                "airpods,wireless-earbuds"),
    (r"\bearbuds|earphones|tws\b",  "wireless-earbuds,bluetooth-earphones"),
    (r"\bheadphones?|headset\b",    "headphones,over-ear-headphones"),
    (r"\bspeaker\b",                "bluetooth-speaker,portable-speaker"),
    (r"\bsoundbar\b",               "soundbar,home-theater"),

    # ── Other electronics ────────────────────────────────────────
    (r"\bpower\s*bank\b",           "powerbank,portable-charger"),
    (r"\bcharger\b",                "phone-charger,usb-charger"),
    (r"\bgaming\s*monitor|monitor\b",  "computer-monitor,gaming-monitor"),
    (r"\bkeyboard\b",               "mechanical-keyboard,computer-keyboard"),
    (r"\bmouse\b",                  "computer-mouse,wireless-mouse"),
    (r"\bdrone|quadcopter\b",       "drone,quadcopter,dji"),
    (r"\bdashcam\b",                "dashcam,car-camera"),
    (r"\bcamera\b",                 "dslr-camera,photography"),
    (r"\btv|television\b",          "smart-tv,television"),

    # ── Fashion — women ──────────────────────────────────────────
    (r"\bsaree|sari\b",             "saree,bangladeshi-fashion,traditional"),
    (r"\bjamdani\b",                "jamdani-saree,bangladeshi-textile"),
    (r"\bsilk\s*chiffon|chiffon\b", "chiffon-saree,silk-fabric,fashion"),
    (r"\bsalwar|kameez|kurti\b",    "salwar-kameez,kurti,ethnic-wear"),
    (r"\babaya|hijab|burka\b",      "abaya,modest-fashion"),
    (r"\bdress\b",                  "dress,women-fashion,fashion"),
    (r"\bmidi|maxi|gown\b",         "midi-dress,women-fashion"),
    (r"\btop\b|blouse\b|tunic\b",   "women-top,blouse"),
    (r"\bheels?|stilettos?\b",      "high-heels,women-shoes"),

    # ── Fashion — men ────────────────────────────────────────────
    (r"\bpanjabi|kurta\b",          "kurta,panjabi,ethnic-wear-men"),
    (r"\bpolo\s*shirt|polo\b",      "polo-shirt,casual-wear-men"),
    (r"\bt\s*-?\s*shirt\b",         "tshirt,casual-tee"),
    (r"\bshirt\b",                  "shirt,formal-shirt,men-shirt"),
    (r"\bchinos?|trousers?\b",      "chinos,men-pants"),
    (r"\bjeans\b",                  "jeans,denim,men-jeans"),
    (r"\bblazer|coat|jacket\b",     "blazer,jacket,outerwear"),
    (r"\bsneakers?\b",              "sneakers,white-sneakers"),
    (r"\bshoes?\b",                 "shoes,leather-shoes"),
    (r"\bsandals?|flip\s*flops?\b", "sandals,flip-flops"),

    # ── Bags / accessories ──────────────────────────────────────
    (r"\bhandbag\b",                "handbag,leather-handbag,women-bag"),
    (r"\bbackpack\b",               "backpack,school-bag"),
    (r"\btote\b",                   "tote-bag,canvas-bag"),
    (r"\bwallet\b",                 "wallet,leather-wallet"),
    (r"\bsunglasses?\b",            "sunglasses,fashion-sunglasses"),
    (r"\bperfume|fragrance\b",      "perfume-bottle,fragrance"),

    # ── Beauty ───────────────────────────────────────────────────
    (r"\blipstick\b",               "lipstick,red-lipstick,makeup"),
    (r"\bfoundation\b",             "foundation-makeup,beauty"),
    (r"\bmascara\b",                "mascara,eye-makeup"),
    (r"\bshampoo\b",                "shampoo-bottle,haircare"),
    (r"\bmoisturizer|moisturiser\b","moisturizer,skincare-cream"),
    (r"\bskincare\b",               "skincare,beauty-routine"),

    # ── Grocery / food ───────────────────────────────────────────
    (r"\brice\b",                   "rice-bag,basmati,grain"),
    (r"\boil|cooking\s*oil\b",      "cooking-oil-bottle,sunflower"),
    (r"\btea\b",                    "tea-bag-box,black-tea"),
    (r"\bcoffee\b",                 "coffee-beans,coffee-cup"),
    (r"\bchocolate\b",              "chocolate-bar,dark-chocolate"),
    (r"\bbiscuit|cookies?\b",       "biscuits,cookies"),
    (r"\bcereal|muesli|oats\b",     "cereal-box,breakfast"),
    (r"\bmilk|dairy\b",             "milk-carton,dairy"),
    (r"\bhoney\b",                  "honey-jar,raw-honey"),
    (r"\bnoodles?\b",               "noodles,instant-noodles"),

    # ── Home / kitchen ──────────────────────────────────────────
    (r"\bair\s*fryer\b",            "air-fryer,kitchen-appliance"),
    (r"\bblender\b",                "kitchen-blender,smoothie"),
    (r"\bmicrowave\b",              "microwave-oven,kitchen"),
    (r"\bvacuum|hoover\b",          "vacuum-cleaner,home-cleaning"),
    (r"\bbedsheet|bed\s*sheet\b",   "bedsheet-set,bedding"),
    (r"\bpillow\b",                 "pillow,bedding"),
    (r"\bcurtains?\b",              "curtains,home-decor"),
    (r"\bcookware|pan|frying\b",    "kitchen-cookware,frying-pan"),
    (r"\bpressure\s*cooker\b",      "pressure-cooker-stainless"),

    # ── Baby / kids ──────────────────────────────────────────────
    (r"\bdiapers?|nappy\b",         "diapers-pack,baby"),
    (r"\bbaby\s*lotion|baby\s*shampoo\b", "baby-toiletries,baby"),
    (r"\bstroller|pram\b",          "baby-stroller,pram"),
    (r"\bbottle.*baby|feeding\s*bottle\b", "baby-feeding-bottle"),
    (r"\btoy|teddy\b",              "toys,plush-teddy,childrens-toys"),
    (r"\blego\b",                   "lego-bricks,colorful-blocks"),

    # ── Health / nutrition ──────────────────────────────────────
    (r"\bwhey\s*protein\b",         "whey-protein-tub,fitness"),
    (r"\bvitamin\b",                "vitamin-bottle,supplements"),
    (r"\bfish\s*oil|omega\b",       "fish-oil-capsules,omega-3"),
    (r"\bmultivitamin\b",           "multivitamin-bottle"),
    (r"\bglucose.*meter|glucometer\b", "glucose-meter,diabetes-care"),
    (r"\bblood\s*pressure|bp\s*monitor\b", "bp-monitor,blood-pressure-cuff"),

    # ── Sports ──────────────────────────────────────────────────
    (r"\byoga\s*mat\b",             "yoga-mat,fitness"),
    (r"\bdumbbell\b",               "dumbbell-pair,weight-training"),
    (r"\btreadmill\b",              "treadmill,gym-equipment"),
    (r"\bbicycle|bike\b",           "bicycle,road-bike"),
    (r"\bfootball|soccer\b",        "football,soccer-ball"),
    (r"\bcricket\s*bat|cricket\b",  "cricket-bat,cricket-equipment"),

    # ── Automotive ──────────────────────────────────────────────
    (r"\bhelmet\b",                 "motorcycle-helmet,full-face"),
    (r"\bengine\s*oil|motor\s*oil\b", "engine-oil-bottle,motor-oil"),
    (r"\btire|tyre\b",              "car-tire,automotive"),
    (r"\bjump\s*starter\b",         "car-jump-starter,car-battery"),
    (r"\bair\s*freshener\b",        "car-air-freshener"),

    # ── Books / stationery ──────────────────────────────────────
    (r"\bnovel|paperback|book\b",   "book,paperback,reading"),
    (r"\bnotebook\b",               "notebook,leather-journal"),
    (r"\bpen\b",                    "ballpoint-pen,fountain-pen"),
    (r"\bcalculator\b",             "scientific-calculator,calculator"),
    (r"\bpencil|colour\s*pencil\b", "colored-pencils,stationery"),
]

GENERIC = "product,shopping,bangladesh-store"


def _fnv1a(seed: str) -> int:
    h = 2166136261
    for ch in seed:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h % 9_999_993


def tags_for(name: str, brand: str | None, slug: str = "") -> str:
    """Pick the best loremflickr tag for a product, falling back to a
    brand-aware generic when no keyword matches.

    Searches across name + brand + slug — the slug often carries the
    product-type word more cleanly (``smartwatch-rose-gold``) than the
    marketing name (``Noise ColorFit Pro 4``)."""
    # Replace dashes with spaces in slug so word-boundary matches fire.
    slug_words = (slug or "").replace("-", " ")
    haystack = f"{name or ''} {brand or ''} {slug_words}".lower()
    for pattern, tag in RULES:
        if re.search(pattern, haystack):
            return tag
    # Brand-aware fallback — eg unknown Apple SKU → "apple,electronics"
    brand_lc = (brand or "").lower().strip()
    if brand_lc:
        slug = re.sub(r"[^a-z0-9]+", "-", brand_lc).strip("-")
        if slug:
            return f"{slug},{GENERIC}"
    return GENERIC


def build_url(name: str, brand: str | None, product_slug: str) -> str:
    tags = tags_for(name, brand, product_slug).replace(" ", "-")
    lock = _fnv1a(f"prod:{product_slug}")
    return f"https://loremflickr.com/900/900/{tags}?lock={lock}"


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        rows = (
            await s.execute(
                text(
                    """
                    SELECT p.id, p.slug, p.name,
                           b.name AS brand_name,
                           pm.id AS media_id, pm.url AS media_url
                      FROM products p
                      LEFT JOIN brands b ON b.id = p.brand_id
                      LEFT JOIN product_media pm
                             ON pm.product_id = p.id AND pm.position = 0
                     WHERE p.status = 'active'
                     ORDER BY p.slug
                    """,
                ),
            )
        ).all()

        updated_media = 0
        inserted_media = 0
        for pid, slug, name, brand, media_id, media_url in rows:
            new_url = build_url(name, brand, slug)
            if media_id is None:
                # Product has no primary image — insert one
                await s.execute(
                    text(
                        """
                        INSERT INTO product_media
                          (id, product_id, kind, url, alt, position, created_at, updated_at)
                        VALUES (gen_random_uuid(), :pid, 'image', :url, :alt, 0, NOW(), NOW())
                        """,
                    ),
                    {"pid": pid, "url": new_url, "alt": name or ""},
                )
                inserted_media += 1
            elif media_url != new_url:
                await s.execute(
                    text(
                        "UPDATE product_media SET url = :url, updated_at = NOW() WHERE id = :mid",
                    ),
                    {"url": new_url, "mid": media_id},
                )
                updated_media += 1

        print(f"products scanned: {len(rows)}")
        print(f"media updated:    {updated_media}")
        print(f"media inserted:   {inserted_media}")


if __name__ == "__main__":
    asyncio.run(main())
