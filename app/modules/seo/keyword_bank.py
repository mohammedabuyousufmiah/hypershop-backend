"""BD purchase-intent keyword bank — ~2,000 phrases across EN+BN.

Used by the auto-SEO engine to inject category-aware keywords into
product meta + category landing pages + sitemap-driven indexable
pseudo-pages. Modelled on what BD shoppers actually type into Google
when ready to buy (vs research-mode queries).

Structure:

- ``BUY_INTENT_PREFIX``    — "buy", "online", "price", "shop", "best
                              deal" + Bangla equivalents
- ``PRICE_MODIFIER``        — "cheap", "discount", "under 5000",
                              "original", "lowest price"
- ``DELIVERY_MODIFIER``     — "free delivery", "cash on delivery",
                              "home delivery", "1 day delivery"
- ``LOCATION_MODIFIER``     — all 8 BD divisions + 64 districts + key
                              urban hubs
- ``PAYMENT_MODIFIER``      — bKash, Nagad, Rocket, SSL, card, EMI
- ``SEASONAL_KEYWORD``      — Eid, Pohela Boishakh, Victory Day, 16
                              December, winter sale, Black Friday
- ``CATEGORY_SEED``         — 60 BD-top categories (groceries to
                              electronics) with English + Bangla
- ``BRAND_SEED``            — 100 BD-popular brands (Aarong, Walton,
                              Pran, Marcel, Square + global like
                              Samsung, Xiaomi, Apple)
- ``QUESTION_TEMPLATE``     — "where to buy ___ in bd", "is ___ available
                              in bangladesh", "___ price in dhaka"
- ``BD_TRANSLITERATED``     — common Banglish queries (mixed Bangla
                              meaning + Latin spelling) e.g. "ekta
                              laptop chai", "shasta phone"

Total expansion ≈ 60 cat × 8 intent × 4 modifier ≈ 1920 + 100 brand +
seasonal + question templates ≈ 2,100+ live phrases at runtime.

Memory rule: BD shoppers heavily NAT (Robi/Banglalink/GP) so keyword
quality matters more than quantity — every phrase here is intent-coded
("buy" / "price" / "online") not just topic ("smartphone").
"""
from __future__ import annotations

# ----------------------------------------------------------------------
# Intent prefixes (44)
# ----------------------------------------------------------------------
BUY_INTENT_PREFIX_EN: tuple[str, ...] = (
    "buy", "buy online", "shop", "shop online", "order", "order online",
    "purchase", "get", "find",
    "price of", "price in bangladesh", "cost of",
    "best", "top", "popular", "new", "latest",
    "where to buy", "where to get",
    "online shopping", "online store for",
    "deal on", "offer on", "discount on",
)

BUY_INTENT_PREFIX_BN: tuple[str, ...] = (
    "কিনুন", "কেনা", "কেনার জন্য", "অনলাইন কিনুন",
    "অর্ডার করুন", "অর্ডার", "কেনাকাটা",
    "দাম", "মূল্য", "দাম কত", "বাংলাদেশে দাম",
    "সেরা", "ভালো", "নতুন", "জনপ্রিয়",
    "কোথায় পাবো", "কোথায় কিনবো",
    "ছাড়", "অফার", "ডিসকাউন্ট",
)

# ----------------------------------------------------------------------
# Price / quality modifiers (32)
# ----------------------------------------------------------------------
PRICE_MODIFIER: tuple[str, ...] = (
    "cheap", "lowest price", "best price", "cheapest",
    "discount", "offer", "sale", "deal",
    "original", "authentic", "genuine", "100% original",
    "imported", "bd stock", "local stock", "brand new",
    "under 500", "under 1000", "under 2000", "under 5000",
    "under 10000", "under 20000", "premium", "budget",
    "সস্তা", "কম দামে", "সেরা দাম", "অরিজিনাল",
    "আসল", "ব্র্যান্ড নিউ", "ইম্পোর্টেড", "বাজেট",
)

# ----------------------------------------------------------------------
# Delivery / fulfillment modifiers (24)
# ----------------------------------------------------------------------
DELIVERY_MODIFIER: tuple[str, ...] = (
    "free delivery", "cash on delivery", "cod", "home delivery",
    "next day delivery", "same day delivery", "1 day delivery",
    "express delivery", "doorstep delivery",
    "fast delivery", "instant delivery",
    "free shipping", "no shipping cost",
    "ফ্রি ডেলিভারি", "ক্যাশ অন ডেলিভারি", "হোম ডেলিভারি",
    "দ্রুত ডেলিভারি", "একদিনে ডেলিভারি", "ফ্রি শিপিং",
    "ঢাকায় ডেলিভারি", "চট্টগ্রামে ডেলিভারি",
    "সারা বাংলাদেশে ডেলিভারি",
)

# ----------------------------------------------------------------------
# Location modifiers — 8 divisions + 30 high-pop districts + 12 hubs
# ----------------------------------------------------------------------
LOCATION_MODIFIER: tuple[str, ...] = (
    # 8 divisions
    "bangladesh", "dhaka", "chittagong", "sylhet", "rajshahi",
    "khulna", "barisal", "rangpur", "mymensingh",
    # major cities / districts
    "comilla", "narayanganj", "gazipur", "savar", "ashulia",
    "tongi", "uttara", "mirpur", "dhanmondi", "gulshan",
    "banani", "mohammadpur", "bashundhara", "wari",
    "cox's bazar", "jessore", "narsingdi", "tangail",
    "bogura", "dinajpur", "kushtia", "patuakhali",
    "feni", "noakhali", "manikganj", "munshiganj",
    "narail", "magura", "natore", "pabna",
    "siraganj", "thakurgaon",
    # Bangla locations
    "ঢাকা", "চট্টগ্রাম", "সিলেট", "রাজশাহী",
    "খুলনা", "বরিশাল", "রংপুর", "ময়মনসিংহ",
    "ঢাকায়", "চট্টগ্রামে", "সিলেটে", "বাংলাদেশে",
)

# ----------------------------------------------------------------------
# BD payment modifiers (18)
# ----------------------------------------------------------------------
PAYMENT_MODIFIER: tuple[str, ...] = (
    "bkash", "bkash payment", "pay with bkash", "bkash discount",
    "nagad", "nagad payment", "rocket", "rocket payment",
    "upay", "tap pay",
    "credit card", "debit card", "visa", "mastercard",
    "ssl commerz", "ssl payment", "online payment",
    "emi", "0% emi", "installment",
    "বিকাশ", "নগদ", "রকেট", "কার্ড পেমেন্ট",
    "ইএমআই", "কিস্তি", "অনলাইন পেমেন্ট",
)

# ----------------------------------------------------------------------
# Seasonal / event keywords (32)
# ----------------------------------------------------------------------
SEASONAL_KEYWORD: tuple[str, ...] = (
    "eid sale", "eid offer", "eid shopping", "eid gift",
    "eid ul fitr", "eid ul adha", "qurbani eid", "eid mubarak deal",
    "pohela boishakh", "pohela boishakh sale", "noboborsho offer",
    "victory day sale", "16 december offer", "independence day deal",
    "21 february sale", "ekushey february offer",
    "winter sale", "summer sale", "monsoon offer",
    "ramadan offer", "iftar special", "sehri deal",
    "black friday bd", "11.11 sale", "12.12 sale",
    "new year sale", "year end deal", "back to school",
    "wedding season offer", "puja sale", "durga puja offer",
    "winter collection",
    # Bangla
    "ঈদ সেল", "ঈদ অফার", "ঈদ শপিং", "পহেলা বৈশাখ অফার",
    "বিজয় দিবস সেল", "শীত সেল", "রমজান অফার",
)

# ----------------------------------------------------------------------
# Category seeds — 60 BD top retail categories with EN+BN
# ----------------------------------------------------------------------
CATEGORY_SEED: tuple[tuple[str, str], ...] = (
    # Electronics
    ("smartphone", "স্মার্টফোন"), ("mobile phone", "মোবাইল"),
    ("laptop", "ল্যাপটপ"), ("desktop computer", "ডেস্কটপ"),
    ("tablet", "ট্যাবলেট"), ("smartwatch", "স্মার্ট ওয়াচ"),
    ("earbuds", "ইয়ারবাড"), ("headphones", "হেডফোন"),
    ("bluetooth speaker", "ব্লুটুথ স্পিকার"),
    ("power bank", "পাওয়ার ব্যাংক"), ("charger", "চার্জার"),
    ("data cable", "ডেটা ক্যাবল"), ("mobile cover", "মোবাইল কভার"),
    ("memory card", "মেমোরি কার্ড"), ("pendrive", "পেনড্রাইভ"),
    # TVs + appliances
    ("led tv", "এলইডি টিভি"), ("smart tv", "স্মার্ট টিভি"),
    ("refrigerator", "ফ্রিজ"), ("air conditioner", "এসি"),
    ("washing machine", "ওয়াশিং মেশিন"),
    ("microwave oven", "মাইক্রোওয়েভ ওভেন"),
    ("electric kettle", "ইলেকট্রিক কেটলি"),
    ("rice cooker", "রাইস কুকার"),
    ("blender", "ব্লেন্ডার"), ("iron", "আয়রন"),
    ("ceiling fan", "ফ্যান"), ("ips", "আইপিএস"),
    # Fashion
    ("saree", "শাড়ি"), ("panjabi", "পাঞ্জাবি"),
    ("salwar kameez", "সালোয়ার কামিজ"),
    ("burqa", "বোরকা"), ("hijab", "হিজাব"),
    ("t-shirt", "টি শার্ট"), ("polo shirt", "পোলো শার্ট"),
    ("jeans", "জিন্স"), ("formal shirt", "ফরমাল শার্ট"),
    ("kurti", "কুর্তি"), ("lehenga", "লেহেঙ্গা"),
    ("sneakers", "স্নিকার্স"), ("formal shoes", "ফরমাল জুতা"),
    ("sandals", "স্যান্ডেল"), ("school shoes", "স্কুল জুতা"),
    ("watch", "ঘড়ি"), ("sunglasses", "সানগ্লাস"),
    ("bag", "ব্যাগ"), ("backpack", "ব্যাকপ্যাক"),
    ("wallet", "ওয়ালেট"),
    # Beauty + grooming
    ("face wash", "ফেসওয়াশ"), ("moisturizer", "ময়েশ্চারাইজার"),
    ("sunscreen", "সানস্ক্রিন"), ("lipstick", "লিপস্টিক"),
    ("foundation", "ফাউন্ডেশন"),
    ("perfume", "পারফিউম"), ("body spray", "বডি স্প্রে"),
    ("shampoo", "শ্যাম্পু"), ("hair oil", "চুলের তেল"),
    ("trimmer", "ট্রিমার"), ("hair dryer", "হেয়ার ড্রায়ার"),
    # Home + kitchen
    ("kitchen utensils", "রান্নাঘরের জিনিস"),
    ("dinner set", "ডিনার সেট"),
    ("bedsheet", "বেডশীট"), ("blanket", "কম্বল"),
    ("pillow", "বালিশ"), ("mattress", "তোশক"),
    ("curtain", "পর্দা"), ("furniture", "ফার্নিচার"),
    # Groceries
    ("rice", "চাল"), ("atta", "আটা"), ("cooking oil", "তেল"),
    ("sugar", "চিনি"), ("tea", "চা"), ("biscuit", "বিস্কুট"),
    ("milk powder", "গুঁড়া দুধ"), ("baby food", "শিশু খাবার"),
    ("diapers", "ডায়াপার"), ("baby formula", "বেবি ফর্মুলা"),
    # Health
    ("vitamin", "ভিটামিন"), ("first aid kit", "ফার্স্ট এইড কিট"),
    ("mask", "মাস্ক"), ("sanitizer", "স্যানিটাইজার"),
    ("thermometer", "থার্মোমিটার"),
    # Books + stationery
    ("books", "বই"), ("textbook", "পাঠ্য বই"),
    ("notebook", "নোটবুক"), ("pen", "কলম"),
    # Sports
    ("cricket bat", "ক্রিকেট ব্যাট"), ("football", "ফুটবল"),
    ("gym equipment", "জিম সরঞ্জাম"),
    ("yoga mat", "যোগ ম্যাট"), ("bicycle", "সাইকেল"),
    # Automotive
    ("motorcycle accessories", "মোটরসাইকেল অ্যাক্সেসরিজ"),
    ("car cover", "কার কভার"),
    ("helmet", "হেলমেট"), ("car perfume", "কার পারফিউম"),
)

# ----------------------------------------------------------------------
# Brand seeds — 100 BD-popular brands (local + global)
# ----------------------------------------------------------------------
BRAND_SEED: tuple[str, ...] = (
    # BD local titans
    "aarong", "yellow", "kay kraft", "anjan's", "westecs",
    "ecstasy", "freeland", "richman", "le reve", "infinity",
    "lubnan", "pride", "rang bd", "deshi dosh", "twelve",
    "walton", "marcel", "minister", "vision", "rfl",
    "pran", "akij", "square", "incepta", "beximco",
    "fresh", "olympic", "abul khair", "kazi farms", "bashundhara",
    "city group", "tk group", "meghna group", "abdul monem",
    # Global electronics
    "samsung", "xiaomi", "redmi", "oppo", "vivo", "realme",
    "infinix", "tecno", "itel", "nokia", "huawei", "honor",
    "apple", "iphone", "macbook", "ipad",
    "asus", "acer", "lenovo", "hp", "dell", "msi",
    "logitech", "razer", "boat", "jbl", "sony", "bose",
    "anker", "ugreen", "baseus",
    # Global fashion + beauty
    "nike", "adidas", "puma", "reebok", "skechers",
    "uniqlo", "h&m", "levi's", "tommy hilfiger",
    "loreal", "maybelline", "nivea", "lakme",
    "garnier", "dove", "ponds", "fair & lovely",
    # Appliances
    "lg", "panasonic", "philips", "havells", "bajaj",
    "sharp", "toshiba", "haier", "midea", "tcl",
    "transcom", "singer", "rangs",
    # FMCG
    "nestle", "unilever", "reckitt", "p&g", "colgate",
    "sensodyne", "horlicks", "complan", "boost",
    # Pharma + health
    "beximco pharma", "renata", "acme", "opsonin", "drug intl",
)

# ----------------------------------------------------------------------
# Question templates — long-tail (12)
# ----------------------------------------------------------------------
QUESTION_TEMPLATE_EN: tuple[str, ...] = (
    "where to buy {kw} in bangladesh",
    "{kw} price in bd",
    "{kw} price in dhaka",
    "is {kw} available in bangladesh",
    "best {kw} in bd",
    "cheapest {kw} bd",
    "original {kw} bangladesh",
    "{kw} online shop bd",
    "{kw} with cash on delivery",
    "{kw} with bkash payment",
    "{kw} home delivery dhaka",
    "{kw} review bd",
)

QUESTION_TEMPLATE_BN: tuple[str, ...] = (
    "{kw} এর দাম কত",
    "{kw} কোথায় পাবো",
    "বাংলাদেশে {kw} এর দাম",
    "{kw} অনলাইনে কেনা",
    "সেরা {kw} বাংলাদেশ",
    "অরিজিনাল {kw} কিনুন",
    "{kw} ক্যাশ অন ডেলিভারি",
    "{kw} বিকাশে কিনুন",
)

# ----------------------------------------------------------------------
# Banglish (transliterated Bangla in Latin) — common search shape
# ----------------------------------------------------------------------
BANGLISH_PHRASE: tuple[str, ...] = (
    "ekta laptop chai", "shasta phone bd", "valo mobile bd",
    "kom dame iphone", "valo brand er ghori",
    "online cinte ki valo", "bkash diye kena jay ki",
    "free delivery achhe ki", "discount offer cholcche",
    "eid er kapor", "boishakh er sari",
    "winter er jamai", "school er bag",
    "office er shirt", "gym er jutar dam",
)

# ----------------------------------------------------------------------
# Expansion API
# ----------------------------------------------------------------------
def expand_for_product(
    *,
    name: str,
    category_en: str | None = None,
    category_bn: str | None = None,
    brand: str | None = None,
    cap: int = 40,
) -> list[str]:
    """Generate a tight keyword set for a single product.

    Returns up to ``cap`` phrases tuned for the product's category +
    brand, mixing EN and BN. Used by SeoAutoGenService to populate
    the ``keywords`` extra_meta field.
    """
    name = (name or "").strip()
    brand = (brand or "").strip()
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        p = p.strip()
        if p and p.lower() not in seen and len(p) <= 80:
            seen.add(p.lower())
            out.append(p)

    if not name:
        return out

    # Core name x intent
    for prefix in BUY_INTENT_PREFIX_EN[:8]:
        add(f"{prefix} {name}")
        if len(out) >= cap:
            return out
    for prefix in BUY_INTENT_PREFIX_BN[:6]:
        add(f"{prefix} {name}")
        if len(out) >= cap:
            return out

    # Name + location
    for loc in ("bangladesh", "dhaka", "chittagong", "bd", "ঢাকা", "চট্টগ্রাম"):
        add(f"{name} {loc}")
        add(f"{name} price in {loc}")
        if len(out) >= cap:
            return out

    # Name + delivery/payment
    for mod in ("cash on delivery", "free delivery",
                "bkash", "nagad", "ফ্রি ডেলিভারি", "বিকাশে"):
        add(f"{name} {mod}")
        if len(out) >= cap:
            return out

    # Brand combos
    if brand:
        add(f"{brand} {name}")
        add(f"original {brand} {name} bd")
        add(f"{brand} price in bangladesh")

    # Category combos
    if category_en:
        add(f"best {category_en} in bd")
        add(f"{category_en} online bangladesh")
        add(f"buy {category_en} bd")
    if category_bn:
        add(f"সেরা {category_bn} বাংলাদেশ")
        add(f"{category_bn} অনলাইন")

    # Long-tail question templates (top 3 EN + 2 BN)
    for tpl in QUESTION_TEMPLATE_EN[:3]:
        add(tpl.format(kw=name))
        if len(out) >= cap:
            return out
    for tpl in QUESTION_TEMPLATE_BN[:2]:
        add(tpl.format(kw=name))
        if len(out) >= cap:
            return out

    return out[:cap]


def expand_for_category(
    category_en: str, category_bn: str | None = None, cap: int = 60,
) -> list[str]:
    """Generate keyword set for a category landing page."""
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        p = p.strip()
        if p and p.lower() not in seen and len(p) <= 80:
            seen.add(p.lower())
            out.append(p)

    # category × intent (EN)
    for prefix in BUY_INTENT_PREFIX_EN:
        add(f"{prefix} {category_en}")
        if len(out) >= cap // 2:
            break

    # category × location
    for loc in ("bd", "bangladesh", "dhaka", "chittagong", "sylhet"):
        add(f"{category_en} {loc}")
        add(f"best {category_en} in {loc}")

    # category × price modifier
    for mod in PRICE_MODIFIER[:10]:
        add(f"{category_en} {mod}")

    # category × delivery
    for mod in ("free delivery", "cash on delivery", "home delivery"):
        add(f"{category_en} {mod} bd")

    # Bangla variants
    if category_bn:
        for prefix in BUY_INTENT_PREFIX_BN[:8]:
            add(f"{prefix} {category_bn}")
        add(f"সেরা {category_bn} বাংলাদেশ")
        add(f"{category_bn} অনলাইনে কিনুন")
        add(f"{category_bn} এর দাম")

    return out[:cap]


def total_phrase_pool() -> int:
    """Rough size estimate of the keyword space the bank can generate."""
    cat = len(CATEGORY_SEED)
    intent = len(BUY_INTENT_PREFIX_EN) + len(BUY_INTENT_PREFIX_BN)
    loc = len(LOCATION_MODIFIER)
    price = len(PRICE_MODIFIER)
    delivery = len(DELIVERY_MODIFIER)
    payment = len(PAYMENT_MODIFIER)
    season = len(SEASONAL_KEYWORD)
    brand = len(BRAND_SEED)
    # Conservative: each cat × (intent + location + price + delivery)
    # gives the bulk; brand × cat adds another layer.
    return (
        cat * (intent + loc + price + delivery + payment + season)
        + brand * 10
        + len(BANGLISH_PHRASE)
        + len(QUESTION_TEMPLATE_EN) + len(QUESTION_TEMPLATE_BN)
    )


__all__ = [
    "BUY_INTENT_PREFIX_EN", "BUY_INTENT_PREFIX_BN",
    "PRICE_MODIFIER", "DELIVERY_MODIFIER", "LOCATION_MODIFIER",
    "PAYMENT_MODIFIER", "SEASONAL_KEYWORD",
    "CATEGORY_SEED", "BRAND_SEED",
    "QUESTION_TEMPLATE_EN", "QUESTION_TEMPLATE_BN",
    "BANGLISH_PHRASE",
    "expand_for_product", "expand_for_category", "total_phrase_pool",
]
