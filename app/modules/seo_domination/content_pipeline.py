"""Daily content velocity — AI-assisted blog + trend roundup + buying guide pipeline.

Target cadence:
  - 1 blog/day      (365/year)
  - 1 trend-roundup/week  (52/year)
  - 4 buying-guides/month (48/year)
  - 8 glossary entries/month (96/year)
  Total: ~560 unique articles/year

Each article carries:
  - Author Person schema (E-E-A-T)
  - 1500-3000 word original body
  - Speakable + Article + Breadcrumb JSON-LD
  - 5+ internal links (via internal_link.py)
  - BN + EN locale variants
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ContentSpec:
    kind: str            # blog | trend_roundup | buying_guide | comparison | glossary
    topic: str
    target_keywords: list[str]
    locale: str
    min_word_count: int
    author_slug: str | None
    schedule_offset_hours: int = 0  # relative to "now"


# Editorial calendar templates
WEEKDAY_TOPICS = {
    "monday":    "Weekly Tech Roundup — Top Gadgets in BD",
    "tuesday":   "Fashion Tuesday — Trends in Bangladesh This Week",
    "wednesday": "Wallet Wednesday — Daily Essentials Deal Recap",
    "thursday":  "Throwback Thursday — Best Sellers Refreshed",
    "friday":    "Fashion Friday — Outfit Inspiration",
    "saturday":  "Saturday Setup — Smart Home Picks",
    "sunday":    "Sunday Read — Long-form Buying Guide",
}


def daily_blog_spec(now: datetime | None = None, locale: str = "en") -> ContentSpec:
    now = now or datetime.now(timezone.utc)
    weekday = now.strftime("%A").lower()
    topic = WEEKDAY_TOPICS.get(weekday, "Daily Hypershop Brief")
    return ContentSpec(
        kind="blog",
        topic=topic,
        target_keywords=[topic.lower().split(" — ")[0]],
        locale=locale,
        min_word_count=1500,
        author_slug=None,  # rotates via author rotation policy
    )


def weekly_trend_roundup_spec(week_iso: str, locale: str = "en") -> ContentSpec:
    return ContentSpec(
        kind="trend_roundup",
        topic=f"Hypershop Trends — Week {week_iso}",
        target_keywords=["bangladesh shopping trends", "weekly deals bd"],
        locale=locale,
        min_word_count=1800,
        author_slug=None,
    )


def buying_guide_spec(category: str, locale: str = "en") -> ContentSpec:
    return ContentSpec(
        kind="buying_guide",
        topic=f"Best {category.title()} Buying Guide for Bangladesh (2026)",
        target_keywords=[
            f"best {category} bangladesh",
            f"{category} buying guide bd",
            f"how to buy {category} in bangladesh",
        ],
        locale=locale,
        min_word_count=2500,
        author_slug=None,
    )


def comparison_spec(left: str, right: str, locale: str = "en") -> ContentSpec:
    return ContentSpec(
        kind="comparison",
        topic=f"{left} vs {right} — Which to Buy in Bangladesh?",
        target_keywords=[f"{left} vs {right}", f"{left} or {right} bd"],
        locale=locale,
        min_word_count=1800,
        author_slug=None,
    )


def glossary_spec(term: str, locale: str = "en") -> ContentSpec:
    return ContentSpec(
        kind="glossary",
        topic=f"What is {term}? — Hypershop Glossary",
        target_keywords=[f"what is {term}", f"{term} meaning"],
        locale=locale,
        min_word_count=600,
        author_slug=None,
    )


def expected_annual_output() -> dict:
    return {
        "blog_per_year":          365,
        "trend_roundup_per_year":  52,
        "buying_guide_per_year":   48,
        "comparison_per_year":     48,
        "glossary_per_year":       96,
        "total_articles_per_year": 609,
        "total_words_per_year":    365 * 1500 + 52 * 1800 + 48 * 2500 + 48 * 1800 + 96 * 600,
    }
