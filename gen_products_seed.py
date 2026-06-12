"""
Hypershop Phase A — 80 product seed generator.

Emits two artifacts to stdout:
  • seed_phase_a_products.sql   — INSERT statements for products + variants
                                  + product_media + search_documents
  • image_manifest.csv          — list of expected image filenames so the
                                  user can drop matching .jpg files into
                                  apps/customer-web/public/products/

All products are attached to the Hypershop Direct seller and the MAIN
warehouse, priced in BDT. Brand tier informs price band; 5–25 % discount
applied to ~60 % of products via compare_at_price.

Run from backend root:
    .venv/Scripts/python.exe gen_products_seed.py
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

HYPERSHOP_DIRECT_SELLER = "6cfcd323-339c-488c-91b0-ccba787972e9"

# (subcategory_slug, brand_slug_or_None, name, short_desc, base_price_bdt, compare_at_or_None)
ROWS: list[tuple[str, str | None, str, str, int, int | None]] = [
    # 1) Electronics & Gadgets
    ("laptops", "walton", "Walton Tamarind EX710G Core i5 15.6\" Laptop", "12th-gen Core i5, 8GB RAM, 512GB NVMe SSD, full-HD display", 64500, 71000),
    ("laptops", "samsung", "Samsung Galaxy Book4 15.6\" FHD Laptop", "Intel Core Ultra 5, 16GB RAM, 512GB SSD, AMOLED display", 119900, 129900),
    ("televisions", "walton", "Walton WD43RK170A 43\" Smart Android TV", "FHD, Android 11, Dolby Audio, voice remote", 38500, 44000),
    ("televisions", "samsung", "Samsung Crystal UHD 43\" 4K Smart TV", "4K HDR, Tizen OS, PurColor, Q-Symphony", 56900, 64500),
    ("audio-speakers", "xiaomi", "Xiaomi Mi Soundbar 2.1 with Bluetooth", "100W output, wireless subwoofer, optical + AUX", 9800, 11500),
    ("audio-speakers", "walton", "Walton WBS-T2200 2.1 Channel Speaker", "60W, USB + Bluetooth + FM, classic wood finish", 4250, None),
    ("cameras", "samsung", "Samsung Galaxy Camera EK-GC100", "21x optical zoom, 16MP, WiFi-enabled compact camera", 18500, None),
    ("cameras", "xiaomi", "Xiaomi 360° Action Camera", "5.7K dual-lens action cam, IP68 waterproof", 16900, 19500),
    ("smart-watches", "samsung", "Samsung Galaxy Watch6 40mm BT", "Wear OS, BIA body composition, sleep coaching", 24500, 27900),
    ("smart-watches", "xiaomi", "Xiaomi Redmi Watch 4", "1.97\" AMOLED, 150+ sport modes, 20-day battery", 8500, 9999),

    # 2) Fashion & Lifestyle
    ("mens-wear", "aarong", "Aarong Men's Cotton Panjabi — Off-white", "Hand-loom cotton panjabi, traditional Bangladeshi tailoring", 2450, None),
    ("mens-wear", None, "Premium Slim-Fit Cotton Polo Shirt — Navy", "Combed cotton, slim-fit, machine-washable", 850, 1100),
    ("womens-wear", "aarong", "Aarong Half-Silk Saree — Maroon Border", "Half-silk Tangail weave, hand-finished tassel", 3850, 4500),
    ("womens-wear", None, "Three-Piece Unstitched Lawn Suit", "Soft lawn cotton, embroidered yoke, dupatta included", 1450, 1900),
    ("footwear", None, "Men's Casual Leather Loafer — Brown", "Genuine leather upper, anti-slip rubber sole", 1850, 2400),
    ("footwear", "aarong", "Aarong Women's Khussa Shoe — Embroidered", "Hand-embroidered ethnic flats, comfort-padded insole", 1250, None),
    ("bags-luggage", None, "20\" Cabin Trolley Luggage Bag — Hard-shell", "ABS shell, four 360° spinner wheels, TSA combination lock", 3650, 4500),
    ("bags-luggage", None, "Men's Office Laptop Backpack 15.6\"", "Water-resistant, USB charging port, anti-theft zip", 1450, 1850),
    ("watches", None, "Casio MTP-V001D Classic Analog Watch", "Stainless steel band, water-resistant 30m", 2450, None),
    ("watches", "xiaomi", "Xiaomi Mi Watch Lite — Black", "GPS, 9-day battery, 5ATM water-resistant", 4850, 5500),

    # 3) Home & Kitchen
    ("kitchenware", "walton", "Walton WPC-2200 Pressure Cooker 5L", "Aluminium body, BSTI-certified safety valve", 1850, 2100),
    ("kitchenware", None, "Non-stick Frying Pan 24cm with Glass Lid", "PFOA-free coating, induction-ready base", 950, 1200),
    ("bedding", "aarong", "Aarong Cotton Bed Sheet Set — Queen", "100 % cotton, fitted sheet + 2 pillow covers, hand-block print", 2450, None),
    ("bedding", None, "Premium Microfibre Pillow — 2 Pack", "Hollow-fibre fill, soft outer, hypoallergenic", 850, 1100),
    ("cleaning", "unilever", "Vim Dishwash Liquid Lemon 1L", "Cuts grease fast, lemon fragrance, gentle on hands", 245, 280),
    ("cleaning", "square-toiletries", "Tibet Detergent Powder 1kg", "High-foam, all-fabric formula", 165, None),
    ("storage", "walton", "Walton 6-Drawer Plastic Storage Cabinet", "Stackable, 5-tier, food-grade plastic", 2850, 3400),
    ("storage", None, "Stainless Steel Spice Jar Set — 9 Pieces", "Food-grade SS, screw-top lids, rotating tray", 1450, 1850),
    ("home-decor", "aarong", "Aarong Hand-painted Nakshi Wall Hanging", "Cotton fabric, traditional Bengal motif, framed", 1850, None),
    ("home-decor", None, "LED Fairy Light String 10m — Warm White", "USB-powered, 8 modes, indoor + balcony use", 450, 650),

    # 4) Beauty & Personal Care
    ("skincare", "dabur", "Dabur Gulabari Rose Water 200ml", "Pure rose distillate, premium toner + face freshener", 145, None),
    ("skincare", "unilever", "Pond's White Beauty Day Cream 50g", "SPF 15, vitamin B3, daily-use brightening cream", 365, 420),
    ("haircare", "marico", "Parachute Coconut Hair Oil 500ml", "100 % pure coconut oil, no preservatives", 285, 320),
    ("haircare", "dabur", "Dabur Amla Hair Oil 275ml", "Amla-enriched, reduces hair fall, classic formulation", 220, None),
    ("makeup", "dabur", "Dabur Vatika Kajal — Black", "Long-lasting, smudge-proof, almond + camphor enriched", 120, None),
    ("makeup", None, "Matte Liquid Lipstick — Set of 6 Shades", "Long-wear, transfer-proof, BD-skin-tone palette", 950, 1400),
    ("fragrance", None, "Oud Royal Premium Attar 12ml", "Alcohol-free, long-lasting, traditional oudh blend", 1450, 1850),
    ("fragrance", None, "Aqua Fresh Eau de Toilette 100ml — Men", "Citrus + musk, 8-hour lasting, gift box", 1850, 2400),
    ("mens-grooming", "marico", "Set Wet Hair Gel Cool Hold 250ml", "Strong-hold styling gel, non-flaky, with cool menthol", 245, None),
    ("mens-grooming", "square-toiletries", "Magic Shaving Foam Sensitive 200ml", "Aloe vera enriched, gentle on sensitive skin", 285, 320),

    # 5) Grocery & Daily
    ("rice-atta", "pran", "PRAN Chinigura Premium Aromatic Rice 5kg", "Premium polao rice, polished + sorted", 1650, 1850),
    ("rice-atta", "aci", "ACI Pure Atta Whole-Wheat 5kg", "Stone-ground whole wheat, fibre-rich", 420, None),
    ("cooking-oil", "pran", "PRAN Soybean Oil 5L Pet Bottle", "Vitamin A + D fortified, refined soybean oil", 985, 1100),
    ("cooking-oil", "aci", "ACI Pure Sunflower Oil 2L", "Cold-pressed, low-cholesterol, heart-friendly", 540, 620),
    ("snacks", "pran", "PRAN Potato Crackers Hot & Spicy 100g — 12 Pack", "Crispy potato crackers, hot-spice masala", 480, 540),
    ("snacks", "nestle", "Nestlé KitKat Wafer Bar — 24 Pack", "Crispy wafer covered in milk chocolate", 720, 840),
    ("beverages", "pran", "PRAN Frooto Mango Drink 1L — 12 Bottle Carton", "Bangladesh's #1 mango juice, no artificial flavour", 960, 1080),
    ("beverages", "nestle", "Nestlé Nescafé Classic Coffee 100g Jar", "100 % pure soluble coffee, rich aroma", 685, 750),
    ("spices", "aci", "ACI Pure Turmeric Powder 200g", "Pure haldi, mill-fresh, no added colour", 95, None),
    ("spices", "pran", "PRAN Mixed Spice Masala 80g — Beef Curry", "Authentic Bengali beef-curry masala blend", 65, 80),

    # 6) Baby & Kids
    ("diapers-wipes", "unilever", "Pampers Baby Dry Pants Size M — 60 Pack", "12-hour leak-protection, soft cotton-like outer", 1750, 1950),
    ("diapers-wipes", "dabur", "Himalaya Gentle Baby Wipes — 72 Pack", "Aloe vera + chamomile, alcohol-free, pH-balanced", 380, 450),
    ("baby-food", "nestle", "Nestlé Cerelac Wheat Apple Cherry 400g", "Stage-2 baby cereal, iron-fortified, 6+ months", 540, 620),
    ("baby-food", "pran", "PRAN Baby Mango Pulp 500g — 6+ Months", "100 % fruit pulp, no added sugar or preservative", 145, None),
    ("toys", None, "Wooden Educational Puzzle — Alphabet Set", "32-piece A–Z puzzle, non-toxic paint, ages 3+", 850, 1100),
    ("toys", None, "Remote Control Racing Car 1:18 Scale", "2.4 GHz remote, rechargeable, headlights", 1650, 2200),
    ("kids-clothing", "aarong", "Aarong Kids Cotton Frock — Pink Floral", "100 % cotton, soft fit, machine-washable", 850, None),
    ("kids-clothing", None, "Boys School T-Shirt Pack of 3 — White", "Combed cotton, half-sleeve, school-uniform compliant", 750, 950),
    ("school", None, "Premium Spiral Notebook A4 — 5 Pack", "80 GSM ruled pages, 200 pages each, hard cover", 650, None),
    ("school", None, "Children's School Backpack — Cartoon Print", "Padded straps, three compartments, water-resistant", 1250, 1650),

    # 7) Mobile & Accessories
    ("smartphones", "samsung", "Samsung Galaxy A15 6GB/128GB — Blue Black", "MediaTek Helio G99, 50 MP triple cam, 5000 mAh", 22500, 24999),
    ("smartphones", "xiaomi", "Xiaomi Redmi Note 13 8GB/256GB — Mint Green", "Snapdragon 685, 108 MP cam, 120 Hz AMOLED", 26900, 29500),
    ("tablets", "samsung", "Samsung Galaxy Tab A9 4GB/64GB WiFi", "8.7\" display, dual speakers, 64 GB storage", 18500, 21000),
    ("tablets", "xiaomi", "Xiaomi Redmi Pad SE 4GB/128GB", "11\" 90 Hz display, Snapdragon 680, quad speakers", 21500, 23999),
    ("power-banks", "xiaomi", "Xiaomi Mi Power Bank 3i 20000 mAh — Black", "Dual USB out, 22.5W fast-charge, three-input", 2250, 2600),
    ("power-banks", "samsung", "Samsung 10000 mAh Fast-Charge Powerbank", "25W PD, USB-C input + output, slim design", 2850, 3200),
    ("earbuds", "xiaomi", "Xiaomi Redmi Buds 4 Active TWS — White", "12mm driver, IPX4, 30-hour battery, Bluetooth 5.3", 1850, 2200),
    ("earbuds", "samsung", "Samsung Galaxy Buds FE — Graphite", "Active Noise Cancelling, 30-hour battery, IPX2", 8500, 9999),
    ("chargers", "xiaomi", "Xiaomi 33W Fast Wall Charger with Type-C Cable", "QC 3.0 + PD 33W, foldable pin, 1m cable included", 1150, 1350),
    ("chargers", None, "Braided USB-C to Lightning Cable 2m — Black", "MFi-style 2m cable, supports 20W PD fast-charge", 480, 650),

    # 8) Health & Wellness
    ("supplements", "dabur", "Dabur Chyawanprash 1kg — Honey Enriched", "Immunity blend with amla, ghee, honey, 40+ herbs", 685, 780),
    ("supplements", "aci", "ACI Vitamin-C 500mg — 60 Tablets", "Daily immunity support, citrus-flavour chewable", 245, None),
    ("fitness-gear", None, "Anti-slip Yoga Mat 6mm — Premium TPE", "183 × 61 cm, eco-TPE, with carry strap", 1450, 1850),
    ("fitness-gear", None, "Adjustable Dumbbell Set 20kg with Connector Bar", "Cast-iron plates, screw-collar, can form barbell", 3850, 4500),
    ("personal-hygiene", "square-toiletries", "Meril Splash Cologne 200ml — Energy", "Long-lasting body cologne, alcohol-free", 285, None),
    ("personal-hygiene", "unilever", "Lifebuoy Total 10 Bar Soap — 4 Pack", "Anti-bacterial protection, 99.9 % germ kill", 195, 220),
    ("first-aid", "aci", "ACI First Aid Kit Box — Family Size", "30+ items: bandages, antiseptic, gauze, scissor", 850, 1100),
    ("first-aid", "dabur", "Dabur Lal Tail Ayurvedic Baby Oil 200ml", "Traditional baby massage oil with sesame + winter cherry", 285, None),
    ("wellness-devices", "lg", "LG Air Purifier PuriCare 360 Mini", "HEPA + carbon filter, 28 m² coverage, app control", 28500, 32500),
    ("wellness-devices", "samsung", "Samsung Digital Body Scale BG-S5000", "150 kg capacity, tempered-glass, auto on/off", 2850, 3200),
]


def slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[’‘“”']", "", s)  # smart-quote, apostrophe
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:120]


def sql_str(v: str | None) -> str:
    if v is None:
        return "NULL"
    return "'" + v.replace("'", "''") + "'"


def main() -> None:
    assert len(ROWS) == 80, f"Expected 80 products, got {len(ROWS)}"

    out = io.StringIO()
    out.write("-- Auto-generated by gen_products_seed.py — DO NOT EDIT BY HAND\n")
    out.write("-- Hypershop Phase A — 80 BD-marketplace products\n")
    out.write("BEGIN;\n\n")

    sku_counters: dict[str, int] = {}
    image_manifest: list[tuple[str, str, str]] = []  # (slug, filename, name)

    for row in ROWS:
        subcat_slug, brand_slug, name, short_desc, base_price, compare_at = row
        slug = slugify(name)
        # SKU pattern: HYP-<subcat>-<NNNN>
        sku_counters[subcat_slug] = sku_counters.get(subcat_slug, 0) + 1
        sku_no = sku_counters[subcat_slug]
        sku = f"HYP-{subcat_slug.upper().replace('-','')}-{sku_no:04d}"
        mother_sku = sku  # one variant per product → same SKU
        image_filename = f"{slug}.jpg"
        image_url = f"/products/{image_filename}"

        # full description: short + concrete bullet style
        description = (
            f"{name}.\n\n"
            f"{short_desc}. Sold and shipped by Hypershop Direct from our Dhaka "
            "main warehouse. Genuine, brand-warrantied product. Cash-on-delivery + "
            "bKash + card payment accepted. Returns within 7 days as per policy."
        )

        # ---- products ----
        out.write(
            "INSERT INTO products ("
            "slug, name, short_description, description, brand_id, category_id, "
            "status, base_currency, tax_class, attributes, search_text, "
            "published_at, is_medicine, requires_prescription, mother_sku, seller_id"
            ") SELECT "
            f"{sql_str(slug)}, {sql_str(name)}, {sql_str(short_desc)}, {sql_str(description)}, "
            f"{('(SELECT id FROM brands WHERE slug=' + sql_str(brand_slug) + ')') if brand_slug else 'NULL'}, "
            f"(SELECT id FROM categories WHERE slug={sql_str(subcat_slug)} AND parent_id IS NOT NULL), "
            "'active', 'BDT', 'standard', '{}'::jsonb, "
            f"{sql_str(name + ' ' + short_desc)}, "
            "(now() AT TIME ZONE 'UTC'), false, false, "
            f"{sql_str(mother_sku)}, '{HYPERSHOP_DIRECT_SELLER}'::uuid "
            "WHERE NOT EXISTS (SELECT 1 FROM products WHERE slug = "
            f"{sql_str(slug)});\n"
        )

        # ---- product_variants (single default) ----
        compare_at_sql = "NULL" if compare_at is None else str(compare_at)
        out.write(
            "INSERT INTO product_variants ("
            "product_id, sku, name, options, price, compare_at_price, sort_order, is_active"
            ") SELECT "
            f"(SELECT id FROM products WHERE slug={sql_str(slug)}), "
            f"{sql_str(sku)}, 'Default', '{{}}'::jsonb, "
            f"{base_price}, {compare_at_sql}, 0, true "
            "WHERE NOT EXISTS (SELECT 1 FROM product_variants WHERE sku = "
            f"{sql_str(sku)});\n"
        )

        # ---- product_media (one image url placeholder) ----
        out.write(
            "INSERT INTO product_media ("
            "product_id, kind, url, alt, position"
            ") SELECT "
            f"(SELECT id FROM products WHERE slug={sql_str(slug)}), "
            f"'image', {sql_str(image_url)}, {sql_str(name)}, 0 "
            "WHERE NOT EXISTS (SELECT 1 FROM product_media WHERE "
            f"product_id = (SELECT id FROM products WHERE slug={sql_str(slug)}) "
            f"AND url = {sql_str(image_url)});\n"
        )

        # ---- search_documents ----
        out.write(
            "INSERT INTO search_documents ("
            "document_type, entity_id, document_key, title, subtitle, body, normalized_text, metadata_json, is_active, boost"
            ") SELECT 'product', "
            f"(SELECT id FROM products WHERE slug={sql_str(slug)}), "
            f"'product:' || (SELECT id FROM products WHERE slug={sql_str(slug)})::text, "
            f"{sql_str(name)}, {sql_str(short_desc)}, {sql_str(description)}, "
            f"lower({sql_str(name + ' ' + short_desc)}), "
            f"jsonb_build_object('slug', {sql_str(slug)}, 'category', {sql_str(subcat_slug)}), "
            "true, 1.0 "
            "WHERE NOT EXISTS (SELECT 1 FROM search_documents WHERE document_type='product' AND entity_id = "
            f"(SELECT id FROM products WHERE slug={sql_str(slug)}));\n"
        )

        out.write("\n")
        image_manifest.append((slug, image_filename, name))

    out.write("COMMIT;\n\n")
    out.write("-- Summary\n")
    out.write("SELECT 'products' AS k, COUNT(*) AS n FROM products\n")
    out.write("UNION ALL SELECT 'variants', COUNT(*) FROM product_variants\n")
    out.write("UNION ALL SELECT 'media', COUNT(*) FROM product_media\n")
    out.write("UNION ALL SELECT 'search_docs', COUNT(*) FROM search_documents WHERE document_type='product';\n")

    # Write SQL file
    sql_path = Path(__file__).parent / "seed_phase_a_products.sql"
    sql_path.write_text(out.getvalue(), encoding="utf-8")
    print(f"WROTE {sql_path}  ({len(out.getvalue())} bytes, {len(ROWS)} products)")

    # Write image manifest CSV
    csv_path = Path(__file__).parent / "image_manifest.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["slug", "image_filename", "product_name"])
        for slug, fn, n in image_manifest:
            w.writerow([slug, fn, n])
    print(f"WROTE {csv_path}  ({len(image_manifest)} rows)")


if __name__ == "__main__":
    main()
