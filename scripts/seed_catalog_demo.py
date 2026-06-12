"""Idempotent seed script: 14 root categories + ~120 curated products.

Mirrors the frontend curated catalogue at
`frontend/apps/customer-web/components/CategoryFallbackProducts.tsx` so
once seeded, the category pages render the SAME products via the real
`/api/v1/catalog/products?category=...` endpoint — no more "No products
in womens fashion yet" empty state.

Run:

    cd backend
    python -m scripts.seed_catalog_demo

Re-running is safe — every insert checks by slug first. New products
added to the CATALOG dict are picked up on the next run; existing rows
are left untouched (so admin edits don't get clobbered).

Photos resolve through `images.unsplash.com` which the customer-web
CSP `img-src` already allows.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import close_engine, get_sessionmaker
from app.modules.catalog.models import (
    Brand,
    Category,
    Product,
    ProductMedia,
    ProductStatus,
    ProductVariant,
)
# Import the Seller model so SQLAlchemy can resolve the products.seller_id
# foreign key when it builds the metadata graph for migrations / seeds.
from app.modules.sellers import models as _sellers_models  # noqa: F401

log = logging.getLogger("seed_catalog_demo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


_FLICKR_BUCKET_TAGS: dict[str, str] = {
    "womens-fashion":  "dress,woman,fashion",
    "mens-fashion":    "menswear,shirt,man",
    "electronics":     "electronics,gadget,phone",
    "beauty-fragrance":"cosmetics,perfume,beauty",
    "home-kitchen":    "kitchen,home,cookware",
    "grocery":         "groceries,food,supermarket",
    "baby":            "baby,infant,nursery",
    "toys":            "toys,playset,children",
    "kids-fashion":    "kids,children,clothing",
    "sports-outdoors": "sports,outdoor,fitness",
    "health-nutrition":"vitamins,supplement,health",
    "stationery":      "notebook,pen,office",
    "books-media":     "books,library,reading",
    "automotive":      "car,automobile,vehicle",
}


def _flickr(slug: str, bucket: str) -> str:
    """Deterministic loremflickr URL — themed by category bucket, locked
    by product slug so the chosen image is stable across runs.

    Once a seller uploads a real photo via the admin image-pipeline
    these URLs are replaced. Until then, every seeded product gets a
    relevant real photograph instead of a random Picsum scenic.
    """
    tags = _FLICKR_BUCKET_TAGS.get(bucket, "product,shop")
    # Cheap deterministic 32-bit hash so `lock=` stays short + stable.
    h = 2166136261
    for ch in f"{tags}|{slug}":
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    lock = h % 9_999_993
    return f"https://loremflickr.com/900/900/{tags}?lock={lock}"


def _u(photo_id: str) -> str:  # legacy shim — left for backwards compat
    return f"https://picsum.photos/seed/hs-{photo_id}/900/900"


class ProductRow(TypedDict, total=False):
    slug: str
    brand: str
    name: str
    img: str
    was: int
    now: int


ROOT_CATEGORIES: list[tuple[str, str]] = [
    ("womens-fashion", "Women's Fashion"),
    ("mens-fashion", "Men's Fashion"),
    ("electronics", "Electronics"),
    ("beauty-fragrance", "Beauty & Fragrance"),
    ("home-kitchen", "Home & Kitchen"),
    ("grocery", "Grocery"),
    ("baby", "Baby"),
    ("toys", "Toys"),
    ("kids-fashion", "Kids' Fashion"),
    ("sports-outdoors", "Sports & Outdoors"),
    ("health-nutrition", "Health & Nutrition"),
    ("stationery", "Stationery"),
    ("books-media", "Books & Media"),
    ("automotive", "Automotive"),
]


# Compact form — one tuple per row keeps the file small. Fields:
# (slug, brand, name, photo_id, was, now)
CATALOG: dict[str, list[tuple[str, str, str, str, int, int]]] = {
    "womens-fashion": [
        ("linen-midi-dress-sage",      "Aarong",       "Aarong Linen Midi Dress · Sage Green · Block-Printed",       "1496747611176-843222e1e57c",  3490, 2490),
        ("silk-chiffon-saree-emerald", "Tangail",      "Tangail Silk Chiffon Saree · Emerald · Hand-Woven Border",   "1610030469983-98e550d6193c",  6900, 5290),
        ("leather-tote-camel",         "Apex",         "Apex Genuine Leather Tote · Camel Tan · 14\" Laptop Fit",    "1584917865442-de89df76afd3",  4290, 3290),
        ("kurti-printed-indigo",       "Sailor",       "Sailor Printed Cotton Kurti · Indigo · Round-Neck",          "1583391733956-6c78276477e2",  1490,  990),
        ("block-heel-sandals-black",   "Bata",         "Bata Block-Heel Sandals · Black · Cushioned Insole",         "1543163521-1bf539c55dd2",     2290, 1690),
        ("denim-jacket-oversized",     "Yellow",       "Yellow Oversized Denim Jacket · Stone-Washed",                "1551488831-00ddcb6c6bd3",     3290, 2490),
        ("gold-plated-jhumka",         "Apurba",       "Apurba 22k Gold-Plated Jhumka Earrings · Festive Set",        "1535632066927-ab7c9ab60908",  1990, 1490),
        ("sunglasses-cat-eye",         "Vincci",       "Vincci Cat-Eye Sunglasses · UV400 · Tortoise Frame",         "1572635196237-14b3f281503f",  1690, 1290),
        ("scarf-pashmina-rose",        "Tangail",      "Tangail Soft Pashmina Scarf · Dusty Rose · 70\" Wrap",       "1601762603339-fd61e28b698a",  1290,  890),
        ("smartwatch-rose-gold",       "Noise",        "Noise ColorFit Pro 4 · Rose-Gold · AMOLED 1.85\"",           "1551816230-ef5deaed4a26",     5490, 3990),
    ],
    "mens-fashion": [
        ("polo-cotton-pique-navy",     "Cats Eye",     "Cats Eye Cotton Pique Polo · Navy · Slim Fit",               "1583743814966-8936f5b7be1a",  1490,  990),
        ("chino-slim-stone",           "Ecstasy",      "Ecstasy Stretch Chino · Slim · Stone Beige",                  "1473966968600-fa801b3a0307",  2290, 1690),
        ("oxford-shirt-white",         "Le Reve",      "Le Reve Oxford Shirt · White · Long-Sleeve · 100% Cotton",   "1602810318383-e386cc2a3ccf",  1990, 1490),
        ("leather-belt-black-35mm",    "Apex",         "Apex Genuine Leather Belt · Black · 35mm · Pin Buckle",      "1624222247344-550fb60583dc",  1690, 1190),
        ("sneakers-running-grey",      "Adidas",       "Adidas Runfalcon 3.0 Running Shoes · Grey/Black · M",        "1542291026-7eec264c27ff",     7990, 6490),
        ("watch-chrono-black-44",      "Fossil",       "Fossil Grant Chrono Watch · 44mm · Black Steel",             "1524805444758-089113d48a6d", 18990,14490),
        ("panjabi-eid-cream",          "Sailor",       "Sailor Embroidered Panjabi · Eid Collection · Cream",        "1622445275576-721325763afe",  3490, 2490),
        ("boxer-trunk-pack-3",         "Jockey",       "Jockey Cotton Boxer Trunks · 3-Pack · Assorted",             "1604176354204-9268737828e4",  1290,  990),
        ("wallet-leather-bifold",      "Yellow",       "Yellow Leather Bifold Wallet · Brown · 8-Card Slots",        "1627123424574-724758594e93",  1490,  990),
        ("windcheater-jacket-navy",    "K-Sports",     "K-Sports Windcheater Jacket · Navy · Water-Repellent",       "1591047139829-d91aecb6caea",  2990, 2290),
    ],
    "electronics": [
        ("anker-soundcore-p20i",       "Soundcore",    "Anker Soundcore P20i Bluetooth Earphones · 10mm Drivers",    "1572569511254-d8f925fe2cbb",   890,  690),
        ("apple-watch-se-44",          "Apple",        "Apple Watch SE · 44mm GPS Aluminium · BD Warranty",          "1523275335684-37898b6baf30", 32990,28490),
        ("sony-wh-1000xm5",            "Sony",         "Sony WH-1000XM5 Wireless Noise Cancelling Headphones",       "1505740420928-5e560c06d30e", 41990,36990),
        ("macbook-air-m3-13",          "Apple",        "Apple MacBook Air M3 · 13\" · 8GB/256GB · Silver",          "1496181133206-80ce9b88a853",142000,128990),
        ("iphone-15-128-blue",         "Apple",        "iPhone 15 · 128GB · Blue · Official BD Warranty",            "1511707171634-5f897ff02aa9",132000,124000),
        ("logitech-mx-master-3s",      "Logitech",     "Logitech MX Master 3S Wireless Mouse · Graphite",            "1527864550417-7fd91fc51a46", 11990, 9290),
        ("keychron-k2-pro",            "Keychron",     "Keychron K2 Pro Wireless Mechanical Keyboard · Hot-swap",    "1587829741301-dc798b83add3", 14990,11290),
        ("samsung-monitor-27-2k",      "Samsung",      "Samsung 27\" 2K 240Hz IPS QHD Monitor · Built-in Speaker",  "1527443224154-c4a3942d3acf", 38990,33990),
        ("powerbank-anker-20000",      "Anker",        "Anker 20000mAh Power Bank · 130W PD · Triple USB",           "1583863788434-e58a36330cf0",  3290, 1988),
        ("echo-dot-5",                 "Amazon",       "Amazon Echo Dot 5th Gen Smart Speaker · Charcoal",           "1543512214-318c7553f230",     5990, 4690),
        ("ipad-air-m2-11",             "Apple",        "Apple iPad Air M2 · 11\" · 128GB Wi-Fi · Space Grey",       "1561154464-82e9adf32764",    79990,72990),
        ("drone-dji-mini-4",           "DJI",          "DJI Mini 4 Pro Drone · 4K HDR · 34-min Flight",              "1473968512647-3e447244af8f",102000,94900),
    ],
    "beauty-fragrance": [
        ("lakme-9to5-foundation",      "Lakme",        "Lakme 9to5 Primer + Matte Liquid Foundation · 25ml",         "1631730486575-6c3d59cb56dc",  1490, 1090),
        ("loreal-paris-mascara",       "L'Oréal",      "L'Oréal Paris Volume Million Lashes Mascara · Black",        "1631214540242-3cd8c4b0b3f4",  1290,  890),
        ("maybelline-fit-me-30",       "Maybelline",   "Maybelline Fit Me Matte Poreless Foundation · Shade 230",    "1620916566398-39f1143ab7be",  1690, 1290),
        ("nivea-rich-nourish-body",    "Nivea",        "Nivea Rich Nourishing Body Lotion · 400ml · All Skin",       "1556228720-195a672e8a03",      690,  490),
        ("dove-shampoo-200ml",         "Dove",         "Dove Intense Repair Shampoo · 200ml · Damaged Hair",          "1556228852-80b6e5eeff06",      480,  360),
        ("gucci-bloom-edp-50",         "Gucci",        "Gucci Bloom Eau de Parfum · 50ml · Floral Original",         "1541643600914-78b084683601", 12500,10990),
        ("yves-saint-laurent-edt",     "YSL",          "YSL Libre Eau de Parfum · 50ml · Women",                     "1592945403244-b3fbafd7f539", 14990,12990),
        ("garnier-bbcream",            "Garnier",      "Garnier BB Cream Miracle Skin Perfector · 30ml",             "1599733589-1cf2c3ecb6f0",      590,  420),
        ("the-body-shop-tea-tree",     "Body Shop",    "The Body Shop Tea Tree Skin Clearing Toner · 250ml",         "1556228453-efd6c1ff04f6",     1690, 1290),
        ("oral-b-pro-2000",            "Oral-B",       "Oral-B Pro 2000 Electric Toothbrush · Rechargeable",         "1559591937-abc3a5f48bb1",     4990, 3990),
    ],
    "home-kitchen": [
        ("philips-air-fryer-hd9252",   "Philips",      "Philips Air Fryer HD9252 · 4.1L · Rapid Air Tech",           "1585515320310-259814833e62", 14990,11990),
        ("instant-pot-duo-7in1",       "Instant Pot",  "Instant Pot Duo 7-in-1 Pressure Cooker · 5.7L",              "1585644156089-ba91c89fa9e5", 11990, 9290),
        ("nespresso-essenza-mini",     "Nespresso",    "Nespresso Essenza Mini Coffee Machine · Black",              "1572119752760-8b88b1d1a8d3", 18990,15990),
        ("dyson-v8-cordless",          "Dyson",        "Dyson V8 Cordless Vacuum Cleaner · 40-min runtime",          "1558317374-067fb5f30001",    56990,48990),
        ("non-stick-frypan-28",        "Hypershop",    "Hypershop Basics Non-Stick Frypan · 28cm · Induction OK",    "1604908554022-89f0fab93473",  1290,  990),
        ("cotton-bedsheet-queen",      "Home Tex",     "Home Tex Cotton Bedsheet Set · Queen · 3-piece · Cream",     "1505693416388-ac5ce068fe85",  1490,  990),
        ("velvet-hangers-50pk",        "Hypershop",    "Hypershop Basics Slim Velvet Non-Slip Hangers · 50-pack",    "1583243567239-3727d6c0bd58",  1150,  820),
        ("led-bulb-9w-6pk",            "Philips",      "Philips 9W LED Bulb · Cool Daylight · 6-pack",               "1565636192335-09e3b6c1e394",   890,  690),
        ("wall-clock-minimal-30cm",    "Yellow",       "Yellow Minimal Wall Clock · 30cm · Silent Sweep",            "1507473885765-e6ed057f782c",   990,  690),
        ("knife-set-stainless-6pc",    "Hypershop",    "Hypershop Basics Stainless Knife Block · 6-piece",           "1593618998160-e34014e67546",  2490, 1890),
    ],
    "grocery": [
        ("olive-oil-extra-virgin-1l",  "Bertolli",     "Bertolli Extra Virgin Olive Oil · 1L · Italy",               "1611171711791-b34b41b1a3d3",  1490, 1190),
        ("basmati-rice-pran-5kg",      "Pran",         "Pran Premium Basmati Rice · 5kg · Long-Grain Aged",          "1586201375761-83865001e8ac",  1290, 1090),
        ("nescafe-gold-200g",          "Nescafé",      "Nescafé Gold Blend Instant Coffee · 200g Jar",               "1559056199-641a0ac8b55e",     1290,  990),
        ("milk-powder-marks-1kg",      "Marks",        "Marks Full-Cream Milk Powder · 1kg Pouch",                   "1572942398-30b6d4f9f59f",      990,  850),
        ("honey-dabur-500g",           "Dabur",        "Dabur 100% Pure Honey · 500g · No Sugar Added",              "1587049352841-fa90237a6ec5",   590,  490),
        ("tea-bags-twinings-100",      "Twinings",     "Twinings English Breakfast Tea · 100 Bags",                  "1576092768241-dec231879fc3",   990,  790),
        ("muesli-quaker-500g",         "Quaker",       "Quaker Fruit & Nut Muesli · 500g Box",                       "1551184451-76b762941ad6",      690,  590),
        ("dark-chocolate-lindt-100g",  "Lindt",        "Lindt Excellence 70% Dark Chocolate · 100g Bar",             "1606312619070-d48b4c652a52",   390,  320),
    ],
    "baby": [
        ("pampers-premium-l-72",       "Pampers",      "Pampers Premium Care Diapers · Size L · 72 Count",           "1515488042361-ee00e0ddd4e4",  2290, 1890),
        ("huggies-wipes-cucumber-72",  "Huggies",      "Huggies Cucumber & Aloe Baby Wipes · 72 Wipes",              "1492725764893-90b379c2b6e7",   290,  220),
        ("johnsons-baby-shampoo-200",  "Johnson's",    "Johnson's Baby No More Tears Shampoo · 200ml",               "1518806118471-f28b20a1d79d",   390,  320),
        ("stroller-foldable-grey",     "Chicco",       "Chicco Lite Way 3 Stroller · Foldable · Anthracite",         "1503777119540-cae744afba39", 24990,21990),
        ("baby-feeder-philips-260",    "Philips Avent","Philips Avent Natural Response Baby Bottle · 260ml",         "1568625365131-079e026a927d",   990,  790),
        ("baby-walker-multifunction",  "Mastela",      "Mastela Multi-Function Baby Walker · Music · Tray",          "1559533089-3b1ce91d97e3",     3990, 2990),
        ("infant-car-seat-grey",       "Chicco",       "Chicco KeyFit 30 Infant Car Seat · Grey",                    "1591736283587-2af2c3ca7da9", 18990,15990),
        ("baby-blanket-organic",       "Mothercare",   "Mothercare Organic Cotton Baby Blanket · 80×100cm",          "1518823526400-c2a78a7c9f60",  1490, 1090),
    ],
    "sports-outdoors": [
        ("yoga-mat-trc-6mm",           "TRC",          "TRC Yoga Mat · 6mm · Non-Slip · Carrying Strap",             "1571902943202-507ec2618e8f",  1490,  990),
        ("dumbbell-set-20kg",          "Domyos",       "Domyos Adjustable Dumbbell Set · 20kg Pair",                 "1517836357463-d25dfeac3438",  5990, 4490),
        ("treadmill-flat-fold",        "Reebok",       "Reebok GT30 Treadmill · Foldable · 12 km/h · 1.5HP",         "1538805060514-97d9cc17730c", 89990,74990),
        ("running-shoes-asics-gel",    "Asics",        "Asics Gel-Contend 8 Running Shoes · Men · Black",            "1542291026-7eec264c27ff",     8490, 6990),
        ("tent-camping-4p-blue",       "Quechua",      "Quechua MH100 Camping Tent · 4 Person · Blue",               "1504280390367-361c6d9f38f4",  6990, 5490),
        ("cricket-bat-ss-kashmir",     "SS",           "SS Magnum Kashmir Willow Cricket Bat · Senior · SH",         "1531415074968-036ba1b575da",  2990, 2290),
        ("football-adidas-tango",      "Adidas",       "Adidas Tango Glider Football · Size 5 · Stitched",           "1543326727-cf6c39e8f84c",     1890, 1490),
        ("water-bottle-stainless-1l",  "Hypershop",    "Hypershop Basics Insulated Steel Water Bottle · 1L",         "1602143407151-7111542de6e8",   990,  690),
    ],
    "toys": [
        ("lego-classic-creative-1500", "LEGO",         "LEGO Classic Creative Bricks · 1500 Pieces",                 "1558060370-d644eed4d0b8",     5990, 4490),
        ("barbie-dreamhouse",          "Barbie",       "Barbie Dreamhouse · 3-Storey Playset · Lift",                "1599682476729-cda196e1ac56", 14990,11990),
        ("rc-car-monster-blue",        "Hot Wheels",   "Hot Wheels RC Monster Truck · Blue · Rechargeable",          "1558618666-fcd25c85cd64",     1990, 1490),
        ("uno-card-game",              "Mattel",       "Mattel UNO Card Game · Family · 2-10 Players",               "1606503153255-59d8b2e4739e",   590,  420),
        ("stuffed-bear-large",         "Hypershop",    "Hypershop Plush Teddy Bear · Large 80cm · Cream",            "1559454403-b8fb88521e44",     1490,  990),
        ("play-doh-mega-pack",         "Play-Doh",     "Play-Doh Modeling Compound · Mega 36-Pack · Colors",         "1587825140708-dfaf72ae4b04",  1190,  890),
        ("puzzle-1000-world-map",      "Ravensburger", "Ravensburger World Map Puzzle · 1000 Pieces",                "1606503153255-59d8b2e4739e",  1490, 1190),
        ("rubik-cube-3x3-speed",       "MoYu",         "MoYu Speed Cube 3×3 · Magnetic · WCA Approved",              "1611042553365-9b101441c135",   690,  490),
    ],
    "kids-fashion": [
        ("kids-tshirt-cartoon-pack3",  "Cats Eye",     "Cats Eye Kids Cartoon Print T-Shirt · 3-Pack · 4-6Y",        "1622445275546-43c0d8f3f51e",  1490,  990),
        ("kids-school-shoes-black",    "Bata",         "Bata Kids School Shoes · Black · Velcro · UK 1-5",           "1606107557195-0a9f33dad4be",  1890, 1490),
        ("girls-frock-floral-pink",    "Le Reve",      "Le Reve Girls Floral Frock · Pink · 5-7Y",                   "1518931308946-21d8a4abdcec",  1790, 1290),
        ("boys-jacket-hooded-grey",    "Sailor",       "Sailor Boys Hooded Jacket · Grey Marl · Fleece-lined",       "1503944168849-8bf86d2c8b9d",  1990, 1490),
        ("school-backpack-skip-hop",   "Skip Hop",     "Skip Hop Kids School Backpack · Zoo Series · Dinosaur",      "1622434641406-a158123450f9",  2490, 1990),
        ("kids-pyjama-cotton-glow",    "Sailor",       "Sailor Kids Glow-in-the-Dark Cotton Pyjama Set · 6-8Y",      "1601925260362-ba61a9eee775",  1290,  890),
        ("kids-sneakers-led-blue",     "Apex",         "Apex Kids LED Light-Up Sneakers · Blue · UK 8-12",           "1551107696-a4b537c892b0",     1890, 1490),
        ("newborn-bodysuit-pack-5",    "Mothercare",   "Mothercare Organic Cotton Bodysuit · 5-Pack · 0-3M",         "1518823526400-c2a78a7c9f60",  1490, 1190),
    ],
    "health-nutrition": [
        ("whey-protein-on-2lb",        "Optimum N.",   "Optimum Nutrition Gold Standard Whey · 2lb · Chocolate",     "1593095948071-474c08aab1ad",  4990, 3990),
        ("multivitamin-centrum-90",    "Centrum",      "Centrum Advance Multivitamin · 90 Tablets",                  "1584308666744-24d5c474f2ae",  1490, 1190),
        ("omega-3-fish-oil-180",       "Nature Made",  "Nature Made Omega-3 Fish Oil 1200mg · 180 Softgels",         "1583209903953-3eb35c4f3196",  1690, 1290),
        ("bp-monitor-omron-m3",        "Omron",        "Omron M3 Comfort Blood Pressure Monitor · Upper Arm",        "1559757175-5700dde675bc",     5490, 4290),
        ("glucometer-accuchek-active", "Accu-Chek",    "Accu-Chek Active Blood Glucose Monitor + 25 strips",         "1576091160550-2173dba0ef64",  1990, 1590),
        ("ensure-vanilla-400g",        "Abbott",       "Abbott Ensure Complete Vanilla Powder · 400g",               "1556228720-195a672e8a03",     1290,  990),
        ("vitamin-c-1000-90tab",       "Now Foods",    "Now Foods Vitamin C-1000 with Rose Hips · 90 Tabs",          "1584017911766-d451b3d0e843",   990,  790),
        ("thermometer-digital-omron",  "Omron",        "Omron Digital Thermometer · Flexible Tip · Fast Read",       "1583912086296-be5b665036d3",   590,  420),
    ],
    "stationery": [
        ("parker-jotter-blue-pen",     "Parker",       "Parker Jotter Ballpoint Pen · Royal Blue · Gift Box",        "1518733057094-95b53143d2a7",  1290,  990),
        ("moleskine-notebook-a5",      "Moleskine",    "Moleskine Classic Notebook · A5 Hardcover · Ruled",          "1531346878377-a5be20888e57",  1490, 1190),
        ("faber-castell-pencils-72",   "Faber-Castell","Faber-Castell Polychromos Color Pencils · 72 Set",           "1572441711281-46f6db40f93f", 12990,10990),
        ("casio-fx-991ex-classwiz",    "Casio",        "Casio FX-991EX ClassWiz Scientific Calculator · Solar",      "1554224155-1696423b8be0",     1690, 1290),
        ("a4-paper-double-a-500",      "Double A",     "Double A A4 Premium Copy Paper · 80gsm · 500 Sheets",        "1586892476739-fc6e4eeb1c4e",   690,  520),
        ("art-set-watercolor-36",      "Camlin",       "Camlin Artist Watercolor Set · 36 Tubes · 5ml",              "1513364776144-60967b0f800f",  1490, 1190),
        ("office-stapler-max-hd",      "MAX",          "MAX HD-50 Heavy Duty Stapler · 50 Sheet Capacity",           "1568393691080-aab26b7af0db",   890,  690),
        ("desk-organizer-bamboo",      "Yellow",       "Yellow Bamboo Desk Organizer · 5 Compartments",              "1505691938895-1758d7feb511",  1290,  990),
    ],
    "books-media": [
        ("atomic-habits-clear",        "Penguin",      "Atomic Habits · James Clear · Hardcover · English",          "1544947950-fa07a98d237f",     1490, 1190),
        ("ikigai-paperback",           "Penguin",      "Ikigai: The Japanese Secret · Paperback",                    "1485217988980-11786ced9454",   690,  520),
        ("rich-dad-poor-dad",          "Plata",        "Rich Dad Poor Dad · Robert Kiyosaki · 20th Anniversary",     "1589998059171-988d887df646",   690,  520),
        ("humayun-ahmed-himu-set",     "Anyaprokash", "Humayun Ahmed Himu Collection · 5-Book Bundle · Bengali",    "1512820790803-83ca734da794",  1290,  990),
        ("harry-potter-box-set",       "Bloomsbury",   "Harry Potter Complete Box Set · 7 Books · Paperback",        "1621944190310-e3cca1564bd7",  6490, 5490),
        ("kindle-paperwhite-11",       "Amazon",       "Amazon Kindle Paperwhite 11th Gen · 8GB · Glare-Free",       "1543002588-bfa74002ed7e",    18990,16490),
        ("shesher-kobita-collected",   "Mitra Ghosh",  "Shesher Kobita · Rabindranath Tagore · Bengali Classic",     "1495446815901-a7297e633e8d",   390,  290),
        ("psychology-of-money",        "Harriman",     "The Psychology of Money · Morgan Housel · Paperback",        "1592496001020-d31bd185dfc6",   890,  690),
    ],
    "automotive": [
        ("car-vacuum-cleaner-12v",     "Black+Decker", "Black+Decker 12V Car Vacuum Cleaner · 18Kpa · Cyclonic",     "1493238792000-8113da705763",  4990, 3990),
        ("dashcam-70mai-a800s-4k",     "70mai",        "70mai A800S 4K Dash Cam · GPS · Sony IMX415 Sensor",         "1503376780353-7e6692767b70", 14990,11990),
        ("car-charger-baseus-65w",     "Baseus",       "Baseus 65W USB-C Car Charger · PD3.0 · 4-Port",              "1591488320449-9b58f9f49bd5",  1490,  990),
        ("tyre-inflator-michelin",     "Michelin",     "Michelin Digital Tyre Inflator · 12V · Auto-Stop",           "1597007030739-6f10c3f9fc8b",  3490, 2790),
        ("jump-starter-noco-gb40",     "NOCO",         "NOCO GB40 Boost Plus 1000A Jump Starter · 12V Lithium",      "1605559424843-9e4c228bf1c2", 12990, 9990),
        ("car-perfume-treefrog-xtreme","Treefrog",     "Treefrog Xtreme Squash Car Air Freshener · 50ml",            "1492144534655-ae79c964c9d7",   590,  420),
        ("motorcycle-helmet-vega-off", "Vega",         "Vega Off Road Helmet · DOT · Glossy Black · M/L/XL",         "1568772585407-9361f9bf3a87",  2990, 2390),
        ("engine-oil-mobil-1-4l",      "Mobil 1",      "Mobil 1 ESP 5W-30 Engine Oil · 4L · Synthetic",              "1487754180451-c456f719a1fc",  4990, 3990),
    ],
}


def _slugify_brand(name: str) -> str:
    out = name.lower()
    for bad, good in (
        ("'", ""), ("&", "and"), ("+", "-plus"),
        (" ", "-"), (".", ""), ("é", "e"), ("/", "-"),
    ):
        out = out.replace(bad, good)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def _mother_sku(slug: str) -> str:
    # Compact 40-char-safe mother SKU. Slug is already kebab-case unique.
    s = slug.upper().replace("-", "")
    return f"HS{s[:36]}"[:40]


async def _ensure_categories(session: AsyncSession) -> dict[str, UUID]:
    """Insert any missing root categories. Returns slug → id map."""
    by_slug: dict[str, UUID] = {}
    for sort_order, (slug, name) in enumerate(ROOT_CATEGORIES):
        existing = (
            await session.execute(
                select(Category).where(Category.slug == slug, Category.parent_id.is_(None)),
            )
        ).scalar_one_or_none()
        if existing is None:
            cat = Category(
                slug=slug,
                name=name,
                sort_order=sort_order,
                is_active=True,
            )
            session.add(cat)
            await session.flush()
            by_slug[slug] = cat.id
            log.info("created category %s", slug)
        else:
            by_slug[slug] = existing.id
    return by_slug


async def _ensure_brand(session: AsyncSession, brand_name: str) -> UUID:
    slug = _slugify_brand(brand_name)
    existing = (
        await session.execute(select(Brand).where(Brand.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    b = Brand(name=brand_name, slug=slug, is_active=True)
    session.add(b)
    await session.flush()
    return b.id


async def _ensure_product(
    session: AsyncSession,
    *,
    cat_id: UUID,
    cat_slug: str,
    slug: str,
    name: str,
    brand_id: UUID,
    photo_id: str,
    was: int,
    now: int,
) -> bool:
    """Insert product + 1 variant + 1 image. Returns True if new."""
    existing = (
        await session.execute(select(Product).where(Product.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        return False
    p = Product(
        slug=slug,
        name=name,
        short_description=name,
        brand_id=brand_id,
        category_id=cat_id,
        status=ProductStatus.ACTIVE,
        base_currency="BDT",
        mother_sku=_mother_sku(slug),
        is_medicine=False,
        requires_prescription=False,
        attributes={},
        search_text=name.lower(),
    )
    session.add(p)
    await session.flush()
    # Single variant — the compare-at-price preserves the discount UI
    # the frontend draws (-NN% pill on the card).
    variant = ProductVariant(
        product_id=p.id,
        sku=f"{_mother_sku(slug)}-V01",
        name="Default",
        price=Decimal(str(now)),
        compare_at_price=Decimal(str(was)) if was and was > now else None,
        sort_order=0,
        is_active=True,
    )
    session.add(variant)
    media = ProductMedia(
        product_id=p.id,
        kind="image",
        url=_flickr(slug, cat_slug),
        alt=name,
        position=0,
    )
    session.add(media)
    return True


async def run() -> tuple[int, int, int]:
    """Returns (new_categories, new_brands, new_products)."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        cat_map = await _ensure_categories(session)
        new_products = 0
        new_brands = 0
        seen_brand_ids: set[UUID] = set()

        for cat_slug, rows in CATALOG.items():
            cat_id = cat_map.get(cat_slug)
            if cat_id is None:
                log.warning("category %s not in ROOT_CATEGORIES, skipping", cat_slug)
                continue
            for slug, brand_name, name, photo_id, was, now in rows:
                brand_id = await _ensure_brand(session, brand_name)
                if brand_id not in seen_brand_ids:
                    seen_brand_ids.add(brand_id)
                created = await _ensure_product(
                    session,
                    cat_id=cat_id,
                    cat_slug=cat_slug,
                    slug=slug,
                    name=name,
                    brand_id=brand_id,
                    photo_id=photo_id,
                    was=was,
                    now=now,
                )
                if created:
                    new_products += 1
        # Best-effort count of new brands (those we just inserted).
        # We compare against pre-loop state via flush + the seen set.
        new_brands = len(seen_brand_ids)
        return len(cat_map), new_brands, new_products


async def main() -> None:
    try:
        cats, brands, products = await run()
        log.info(
            "seed complete · %d categories present · %d brands touched · "
            "%d new products inserted",
            cats,
            brands,
            products,
        )
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(main())
