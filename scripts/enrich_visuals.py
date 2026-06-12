"""Populate brand logos + category images + homepage banners with
themed loremflickr photos. Idempotent — skips rows that already
have a non-empty URL.

Run: python -m scripts.enrich_visuals
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

# Manual .env load (bypasses MSYS path conversion)
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from app.core.db.uow import UnitOfWork  # noqa: E402
from app.core.time import utc_now  # noqa: E402
from app.modules.catalog.models import Brand, Category  # noqa: E402
from app.modules.seo.models import HomepageBanner  # noqa: E402


def _lock(seed: str) -> int:
    h = 2166136261
    for ch in seed:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h % 9_999_993


def themed(tags: str, seed: str, w: int = 300, h: int = 300) -> str:
    safe_tags = tags.replace(" ", "-")
    return f"https://loremflickr.com/{w}/{h}/{safe_tags}?lock={_lock(seed)}"


BRAND_TAGS = {
    "Apple": "apple-logo,silver-electronics",
    "Samsung": "samsung-phone,android",
    "Sony": "sony-headphones,audio",
    "Anker": "powerbank,charger-cable",
    "Soundcore": "earphones,wireless-buds",
    "Adidas": "adidas-sneaker,sportswear",
    "Nike": "nike-sneaker,running-shoes",
    "Puma": "puma-shoes,sportswear",
    "Asics": "running-shoes,athletic",
    "Reebok": "treadmill,gym-equipment",
    "Fossil": "wristwatch,leather-strap",
    "Herschel": "backpack,canvas-bag",
    "Bata": "leather-shoes,formal",
    "Apex": "formal-shoes,leather",
    "Aarong": "saree,bangladesh-textile",
    "Tangail": "silk-saree,handloom",
    "Cats Eye": "polo-shirt,menswear",
    "Sailor": "casual-shirt,menswear",
    "Ecstasy": "chino-pants,menswear",
    "Le Reve": "dress,floral-fashion",
    "Yellow": "casual-jacket,denim",
    "Vincci": "sunglasses,cat-eye",
    "Apurba": "earrings,gold-jewelry",
    "Noise": "smartwatch,fitness-band",
    "Jockey": "underwear-pack,white",
    "K-Sports": "windbreaker,sportswear",
    "Pampers": "diapers-pack,baby",
    "Huggies": "baby-wipes,pack",
    "Johnson's": "baby-shampoo,toiletries",
    "Chicco": "baby-stroller,grey",
    "Philips Avent": "baby-feeding-bottle",
    "Mastela": "baby-walker,colorful",
    "Mothercare": "newborn-clothes,white",
    "Skip Hop": "kids-backpack,colorful",
    "LEGO": "lego-bricks,colorful",
    "Barbie": "barbie-doll,playset",
    "Hot Wheels": "toy-car,collectible",
    "Mattel": "card-game-box",
    "Play-Doh": "play-doh,clay-set",
    "Ravensburger": "jigsaw-puzzle,1000pc",
    "MoYu": "rubiks-cube,speedcube",
    "Optimum N.": "whey-protein-tub",
    "Centrum": "multivitamin-bottle",
    "Nature Made": "fish-oil-supplement",
    "Omron": "blood-pressure-monitor",
    "Accu-Chek": "glucose-meter,diabetes",
    "Abbott": "ensure-tin,nutrition",
    "Now Foods": "vitamin-c-bottle",
    "Lakme": "lipstick,red",
    "Maybelline": "mascara,foundation",
    "Nivea": "moisturizer-bottle",
    "Dove": "shampoo-bottle,beauty",
    "Gucci": "perfume-bottle,luxury",
    "YSL": "perfume-glass-bottle",
    "Garnier": "bb-cream-tube",
    "Body Shop": "tea-tree-toner",
    "Oral-B": "electric-toothbrush",
    "Bertolli": "olive-oil-bottle",
    "Pran": "rice-bag,grain",
    "Marks": "milk-powder-tin",
    "Dabur": "honey-jar,glass",
    "Twinings": "tea-bag-box",
    "Quaker": "oats-muesli-cereal",
    "Lindt": "dark-chocolate-bar",
    "Philips": "air-fryer-kitchen",
    "Instant Pot": "pressure-cooker-stainless",
    "Nespresso": "espresso-machine,black",
    "Dyson": "stick-vacuum-cleaner",
    "Hypershop": "kitchen-essentials-pack",
    "Home Tex": "bedsheet-set,bedding",
    "TRC": "yoga-mat,fitness",
    "Domyos": "dumbbell-pair,gym",
    "Quechua": "camping-tent,outdoor",
    "Parker": "ballpoint-pen,gift-pen",
    "Moleskine": "leather-notebook",
    "Faber-Castell": "colored-pencils-set",
    "Casio": "scientific-calculator",
    "Double A": "copy-paper-ream",
    "Camlin": "watercolor-tubes-set",
    "Penguin": "paperback-book",
    "Plata": "self-help-book-cover",
    "Anyaprokash": "bengali-novel-cover",
    "Bloomsbury": "harry-potter-set",
    "Amazon": "kindle-paperwhite",
    "Mitra Ghosh": "tagore-classic-book",
    "Harriman": "finance-book-paperback",
    "Black+Decker": "12v-car-vacuum",
    "70mai": "dashcam-windshield",
    "Baseus": "65w-usb-charger",
    "Michelin": "tire-inflator,12v",
    "NOCO": "jump-starter-pack",
    "Treefrog": "car-air-freshener",
    "Vega": "motorcycle-helmet-black",
    "Mobil 1": "engine-oil-bottle-4l",
    "Logitech": "wireless-mouse",
    "Keychron": "mechanical-keyboard",
    "Sony": "sony-wh-headphones",
    "DJI": "drone-quadcopter",
    "Apple": "apple-logo,silver",
    "Galaxy": "samsung-galaxy",
    "Xiaomi": "xiaomi-phone,redmi",
}

CATEGORY_TAGS = {
    "womens-fashion":  "dress,pink,floral,fashion",
    "mens-fashion":    "menswear,shirt,man",
    "electronics":     "smartphone,iphone,gadget",
    "beauty-fragrance":"skincare,cosmetics,beauty",
    "home-kitchen":    "kitchen,cookware,utensils",
    "grocery":         "groceries,vegetables,supermarket",
    "baby":            "baby-stroller,nursery",
    "toys":            "lego-toys,children",
    "kids-fashion":    "kids-clothing,baby-teddy",
    "sports-outdoors": "sports,fitness,outdoor",
    "health-nutrition":"vitamins,supplement,pills",
    "stationery":      "notebook,pen,office",
    "books-media":     "books,library,reading",
    "automotive":      "car,automobile,vehicle",
}

BANNERS = [
    ("Hypershop Mega Sale",       "Up to 70% off across electronics, fashion & home",                  "sale,shopping,discount-store",      "/deals"),
    ("Eid Collection — Now Live", "Aarong saree, Tangail panjabi, festive picks", "eid-fashion,festive-bangladesh", "/c/womens-fashion"),
    ("Electronics Mega Week",     "iPhone, MacBook, Samsung — official BD warranty","smartphone,laptop,electronics", "/c/electronics"),
    ("Free Grocery Delivery in Dhaka", "Rice, oil, dairy delivered before 4pm",   "groceries,supermarket-shopping",   "/c/grocery"),
]


async def go() -> None:
    uow = UnitOfWork()
    async with uow.transactional() as session:
        # 1. Brand logos
        brand_rows = (await session.execute(select(Brand))).scalars().all()
        bcount = 0
        for b in brand_rows:
            if b.logo_url:
                continue
            tags = BRAND_TAGS.get(b.name, "logo,brand-store")
            b.logo_url = themed(tags, "brand:" + b.name, 200, 200)
            bcount += 1
        print(f"  brands updated: {bcount}/{len(brand_rows)}")

        # 2. Categories — the schema has no image columns. The
        # storefront's CategoryShowcase already renders themed Flickr
        # photos client-side via the slug-keyed TILE_TAGS map; no DB
        # column needed. Skip.
        _ = CATEGORY_TAGS  # keep the table for reference / future migration
        print("  categories: no image columns in schema, skipped (CategoryShowcase handles client-side)")

        # 3. Homepage banners
        existing = (await session.execute(select(HomepageBanner))).scalars().all()
        if not existing:
            for title, sub, tags, target in BANNERS:
                session.add(HomepageBanner(
                    title=title,
                    subtitle=sub,
                    image_url=themed(tags, "banner:" + title, 1200, 400),
                    mobile_image_url=themed(tags, "banner-m:" + title, 600, 400),
                    target_url=target,
                    alt_text=title,
                    is_active=True,
                    sort_order=BANNERS.index((title, sub, tags, target)) + 1,
                    valid_from=utc_now(),
                ))
            print(f"  banners inserted: {len(BANNERS)}")
        else:
            print(f"  banners already exist: {len(existing)}")


if __name__ == "__main__":
    asyncio.run(go())
