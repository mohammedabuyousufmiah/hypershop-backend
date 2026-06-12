"""Pure-function SEO agent classes.

Each agent has a deterministic ``fallback`` — when ``OPENAI_API_KEY``
is unset (or the OpenAI call errors), the agent returns a real,
usable output built from the input. This is NOT a stub: the fallback
output is shaped exactly like a successful LLM response and downstream
code (service.py) writes it verbatim into ``seo_agent_runs.output_payload``.

Agents:
  - KeywordIntelligenceAgent    classify intent + suggest cluster + slug
  - LocalLandingPageAgent       build title/meta/h1/sections/faq brief
  - SchemaAgent                 map page-type → schema.org types list
  - ReviewTrustAgent            return policy rules + task list
  - ImprovementAgent            propose strategy based on rank position
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger("hypershop.seo.agents")


@dataclass(frozen=True)
class SEOAgentSettings:
    """Runtime config for the SEO agents.

    Reads from process env (OPENAI_API_KEY / SEO_OPENAI_MODEL) and falls
    back to Hypershop's settings.seo_site_url for the site base URL.
    """

    openai_api_key: str | None
    openai_model: str
    site_base_url: str
    default_country: str


@lru_cache
def get_seo_agent_settings() -> SEOAgentSettings:
    s = get_settings()
    return SEOAgentSettings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("SEO_OPENAI_MODEL", "gpt-4.1-mini"),
        site_base_url=(
            os.getenv("SEO_SITE_BASE_URL")
            or getattr(s, "seo_site_url", "https://www.hypershop.com.bd")
        ).rstrip("/"),
        default_country=os.getenv("SEO_DEFAULT_COUNTRY", "Bangladesh"),
    )


class OpenAISEOClient:
    """Thin wrapper around the OpenAI chat-completions API.

    No API key → return the fallback dict the caller already prepared.
    API errors → return the fallback dict with an ``openai_error`` key
    added so operators can spot failures in seo_agent_runs.output_payload.
    """

    def __init__(self) -> None:
        self.settings = get_seo_agent_settings()

    def generate_json(
        self, system: str, user: str, fallback: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.settings.openai_api_key:
            return fallback
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.settings.openai_api_key)
            response = client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            return json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "openai_seo_call_failed",
                error_class=exc.__class__.__name__,
            )
            return {**fallback, "openai_error": exc.__class__.__name__}


# ============================================================
#  KeywordIntelligenceAgent
# ============================================================
class KeywordIntelligenceAgent:
    name = "keyword_intelligence_agent"

    def run(self, keyword: str, location: str) -> dict[str, Any]:
        fallback = {
            "intent": "transactional",
            "target_page_type": (
                "local_landing_page"
                if location.lower() != "bangladesh"
                else "national_landing_page"
            ),
            "keyword_cluster": [
                keyword,
                f"{keyword} price",
                f"{keyword} cash on delivery",
            ],
            "recommended_url_slug": (
                keyword.lower().replace(" ", "-").replace("bd", "bangladesh")[:90]
            ),
        }
        return OpenAISEOClient().generate_json(
            "You are a white-hat ecommerce SEO analyst. Return strict JSON only.",
            f"Analyze keyword {keyword!r} for Hypershop ecommerce in {location}.",
            fallback,
        )


# ============================================================
#  LocalLandingPageAgent
# ============================================================
class LocalLandingPageAgent:
    name = "local_landing_page_agent"

    def run(self, keyword: str, location: str) -> dict[str, Any]:
        settings = get_seo_agent_settings()
        slug = (
            keyword.lower().replace(" ", "-").replace("bd", "bangladesh")[:90]
        )
        fallback = {
            "url": f"{settings.site_base_url}/{slug}",
            "title": f"{keyword.title()} | Fast Delivery & COD | Hypershop",
            "meta_description": (
                f"Shop with Hypershop for {keyword} in {location}. "
                "Fast delivery, COD, trusted sellers and secure checkout."
            ),
            "h1": f"{keyword.title()} with Hypershop",
            "sections": [
                "Why shop with Hypershop",
                "Fast delivery",
                "COD and online payment",
                "Popular categories",
                "FAQ",
            ],
            "faq": [
                {
                    "q": f"Does Hypershop support {keyword}?",
                    "a": (
                        "Yes, Hypershop supports online shopping with "
                        "delivery and secure checkout."
                    ),
                },
                {
                    "q": "Is COD available?",
                    "a": (
                        "COD availability depends on product, seller and "
                        "delivery location."
                    ),
                },
            ],
            "internal_links": [
                "/kids-baby", "/electronics", "/beauty", "/fashion",
            ],
        }
        return OpenAISEOClient().generate_json(
            "Return JSON SEO landing page brief. No fake claims.",
            f"Create local SEO page brief for {keyword} in {location}.",
            fallback,
        )


# ============================================================
#  SchemaAgent
# ============================================================
class SchemaAgent:
    name = "schema_agent"

    def run(self, page_type: str, keyword: str) -> dict[str, Any]:
        fallback = {
            "schema_types": (
                ["WebPage", "BreadcrumbList", "FAQPage"]
                if page_type != "product"
                else ["Product", "Offer", "BreadcrumbList", "Review"]
            ),
            "json_ld_template": {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": keyword,
            },
            "validation_required": True,
        }
        return OpenAISEOClient().generate_json(
            "Return schema.org JSON-LD guidance as JSON only.",
            f"Generate schema plan for {page_type} targeting {keyword}.",
            fallback,
        )


# ============================================================
#  ReviewTrustAgent
# ============================================================
class ReviewTrustAgent:
    """Deterministic-only agent — no LLM call.

    Returns a fixed policy + task list because operators need consistent
    review trust rules regardless of LLM availability.
    """

    name = "review_trust_agent"

    def run(self, keyword: str) -> dict[str, Any]:
        return {
            "rules": [
                "Verified buyers only",
                "No AI-generated fake reviews",
                "Manual moderation for spam/abuse",
            ],
            "tasks": [
                "Send review request after delivered order",
                "Show return/refund policy on target page",
                "Add seller trust and support information",
            ],
            "risk": (
                "Fake reviews can damage trust and violate platform policy."
            ),
            "keyword": keyword,
        }


# ============================================================
#  ImprovementAgent
# ============================================================
class ImprovementAgent:
    """Deterministic strategy picker keyed on current rank position."""

    name = "rank_improvement_agent"

    def run(
        self, keyword: str, current_position: int | None = None,
    ) -> dict[str, Any]:
        if current_position is None:
            strategy = "setup_tracking"
        elif current_position <= 3:
            strategy = "defend_position"
        elif current_position <= 10:
            strategy = "push_top3"
        else:
            strategy = "major_improvement"
        return {
            "strategy": strategy,
            "keyword": keyword,
            "tasks": [
                "Improve title/meta CTR",
                "Add internal links",
                "Add FAQ schema",
                "Refresh content",
                "Collect verified reviews",
            ],
        }
