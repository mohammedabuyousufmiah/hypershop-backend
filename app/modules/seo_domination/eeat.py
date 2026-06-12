"""E-E-A-T helpers — Experience, Expertise, Authoritativeness, Trustworthiness.

Emits Person + Article + Organization schema with proper sameAs chains.
"""
from __future__ import annotations

from typing import Any


def author_person_schema(profile: dict) -> dict:
    """Build Person JSON-LD for an AuthorProfile row.

    profile keys: slug, full_name, title_role, avatar_url, bio_en,
                  expertise_areas, credentials, social_links, wikidata_qid
    """
    same_as = []
    socials = profile.get("social_links") or {}
    for key in ("linkedin", "twitter", "github", "instagram", "youtube"):
        if socials.get(key):
            same_as.append(socials[key])
    if profile.get("wikidata_qid"):
        same_as.append(f"https://www.wikidata.org/wiki/{profile['wikidata_qid']}")

    schema = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": profile["full_name"],
        "jobTitle": profile["title_role"],
        "url": f"https://hypershop.com.bd/author/{profile['slug']}",
        "description": profile["bio_en"],
        "knowsAbout": profile.get("expertise_areas") or [],
        "sameAs": same_as,
        "worksFor": {
            "@type": "Organization",
            "name": "Hypershop",
            "url": "https://hypershop.com.bd",
        },
    }
    if profile.get("avatar_url"):
        schema["image"] = profile["avatar_url"]
    if profile.get("credentials"):
        schema["hasCredential"] = [
            {
                "@type": "EducationalOccupationalCredential",
                "credentialCategory": c.get("type", "certification"),
                "recognizedBy": {"@type": "Organization", "name": c.get("issuer", "")},
                "validIn": str(c.get("year", "")),
            }
            for c in profile["credentials"]
        ]
    return schema


def article_schema_with_author(
    *,
    headline: str,
    url: str,
    image_url: str,
    published_iso: str,
    modified_iso: str,
    author_profile: dict,
    body_word_count: int,
    description: str,
    locale: str = "en",
) -> dict:
    """Article schema with embedded Person + Publisher — full E-E-A-T."""
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": headline,
        "image": [image_url],
        "datePublished": published_iso,
        "dateModified": modified_iso,
        "inLanguage": "bn" if locale == "bn" else "en",
        "wordCount": body_word_count,
        "description": description,
        "url": url,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "author": author_person_schema(author_profile),
        "publisher": {
            "@type": "Organization",
            "name": "Hypershop",
            "url": "https://hypershop.com.bd",
            "logo": {
                "@type": "ImageObject",
                "url": "https://hypershop.com.bd/logo.png",
                "width": 200,
                "height": 60,
            },
        },
    }


def about_page_schema(
    *,
    org_name: str = "Hypershop",
    url: str = "https://hypershop.com.bd/about",
    founding_date: str = "2024-01-01",
    founders: list[str] | None = None,
    employee_count: int = 50,
    contact_telephone: str = "+8801911740672",
    contact_email: str = "hello@hypershop.com.bd",
) -> dict:
    """AboutPage schema with full Organization E-E-A-T signals."""
    return {
        "@context": "https://schema.org",
        "@type": "AboutPage",
        "url": url,
        "mainEntity": {
            "@type": "Organization",
            "name": org_name,
            "url": "https://hypershop.com.bd",
            "logo": "https://hypershop.com.bd/logo.png",
            "foundingDate": founding_date,
            "founders": [{"@type": "Person", "name": n} for n in (founders or [])],
            "numberOfEmployees": {
                "@type": "QuantitativeValue",
                "value": employee_count,
            },
            "contactPoint": [{
                "@type": "ContactPoint",
                "telephone": contact_telephone,
                "email": contact_email,
                "contactType": "customer service",
                "areaServed": "BD",
                "availableLanguage": ["en", "bn"],
            }],
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "Banani, Dhaka-1213",
                "addressLocality": "Dhaka",
                "addressCountry": "BD",
            },
        },
    }


def expert_review_schema(
    *,
    product_url: str,
    product_name: str,
    rating: float,
    review_body: str,
    author_profile: dict,
    published_iso: str,
) -> dict:
    """Authoritative expert Review with credentialed Person author."""
    return {
        "@context": "https://schema.org",
        "@type": "Review",
        "itemReviewed": {
            "@type": "Product",
            "name": product_name,
            "url": product_url,
        },
        "reviewRating": {
            "@type": "Rating",
            "ratingValue": rating,
            "bestRating": 5,
            "worstRating": 1,
        },
        "reviewBody": review_body,
        "datePublished": published_iso,
        "author": author_person_schema(author_profile),
    }
