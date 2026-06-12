"""Knowledge Graph entity linking — Wikidata + GeoNames sameAs chains.

Cross-references local entities (brand, city, product, person) with global
identifiers so Google's Knowledge Graph picks them up.

Pre-seeded sameAs for common BD entities:
  - Cities: Dhaka (Q1354), Chittagong (Q376033), Sylhet (Q221878), ...
  - Brands: Samsung (Q20716), Apple (Q312), Xiaomi (Q1062265), ...
"""
from __future__ import annotations

CITY_WIKIDATA = {
    "dhaka":       ("Q1354",    "https://en.wikipedia.org/wiki/Dhaka",       "https://bn.wikipedia.org/wiki/%E0%A6%A2%E0%A6%BE%E0%A6%95%E0%A6%BE", 1185241),
    "chittagong":  ("Q376033",  "https://en.wikipedia.org/wiki/Chittagong",  None, 1205733),
    "sylhet":      ("Q221878",  "https://en.wikipedia.org/wiki/Sylhet",      None, 1185098),
    "khulna":      ("Q189643",  "https://en.wikipedia.org/wiki/Khulna",      None, 1336135),
    "rajshahi":    ("Q244281",  "https://en.wikipedia.org/wiki/Rajshahi",    None, 1185188),
    "barishal":    ("Q257864",  "https://en.wikipedia.org/wiki/Barisal",     None, 1185204),
    "rangpur":     ("Q189641",  "https://en.wikipedia.org/wiki/Rangpur,_Bangladesh", None, 1185216),
    "mymensingh":  ("Q221780",  "https://en.wikipedia.org/wiki/Mymensingh",  None, 1185155),
    "comilla":     ("Q244399",  "https://en.wikipedia.org/wiki/Comilla",     None, 1185094),
    "narayanganj": ("Q461015",  "https://en.wikipedia.org/wiki/Narayanganj", None, 1185175),
    "gazipur":     ("Q461052",  "https://en.wikipedia.org/wiki/Gazipur",     None, 1185125),
    "cox-bazar":   ("Q221836",  "https://en.wikipedia.org/wiki/Cox%27s_Bazar", None, 1185104),
}

BRAND_WIKIDATA = {
    "samsung":   "Q20716",     # Samsung
    "apple":     "Q312",        # Apple Inc.
    "xiaomi":    "Q1062265",
    "realme":    "Q42459389",
    "oppo":      "Q4915800",
    "vivo":      "Q42459366",
    "huawei":    "Q15820",
    "oneplus":   "Q23069183",
    "google":    "Q20800404",   # Google Pixel
    "nokia":     "Q1418",
    "asus":      "Q41368",
    "lenovo":    "Q43358",
    "hp":        "Q478214",
    "dell":      "Q116657",
    "acer":      "Q40912",
    "msi":       "Q1525791",
    "nike":      "Q483915",
    "adidas":    "Q3895",
    "puma":      "Q157064",
    "jbl":       "Q5949144",
    "sony":      "Q41187",
    "logitech":  "Q170143",
    "amazfit":   "Q66031745",
    "garmin":    "Q487409",
}


def city_sameas(city_slug: str) -> dict:
    """Return Wikidata + Wikipedia identifiers for a BD city."""
    entry = CITY_WIKIDATA.get(city_slug.lower())
    if not entry:
        return {}
    qid, wiki_en, wiki_bn, geonames = entry
    same_as = [f"https://www.wikidata.org/wiki/{qid}", wiki_en]
    if wiki_bn:
        same_as.append(wiki_bn)
    return {
        "wikidata_qid": qid,
        "wikipedia_url_en": wiki_en,
        "wikipedia_url_bn": wiki_bn,
        "geonames_id": geonames,
        "external_same_as": same_as,
    }


def brand_sameas(brand_slug: str) -> dict:
    qid = BRAND_WIKIDATA.get(brand_slug.lower())
    if not qid:
        return {}
    return {
        "wikidata_qid": qid,
        "external_same_as": [f"https://www.wikidata.org/wiki/{qid}"],
    }


def place_schema(city_slug: str) -> dict | None:
    """Build Schema.org Place block for a city — adds geo context to pages."""
    info = city_sameas(city_slug)
    if not info:
        return None
    return {
        "@type": "Place",
        "name": city_slug.title().replace("-", " "),
        "address": {"@type": "PostalAddress", "addressCountry": "BD"},
        "sameAs": info["external_same_as"],
    }


def organization_brand_schema(brand_slug: str, display_name: str) -> dict | None:
    info = brand_sameas(brand_slug)
    if not info:
        return None
    return {
        "@type": "Brand",
        "name": display_name,
        "sameAs": info["external_same_as"],
    }
