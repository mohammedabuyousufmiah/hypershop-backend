"""Backlink outreach pipeline — BD media + brand partnerships.

Targets handpicked + auto-discovered BD-relevant domains:
  - Tech blogs (TechShohor, Daily Tech BD, NextWeb BD)
  - Lifestyle (Bproperty, Ahmedia)
  - News (Prothom Alo tech, Daily Star tech)
  - University career pages
  - Brand partnership co-marketing

Pitch templates carry pre-baked story hooks + media kit links.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutreachTarget:
    domain: str
    contact_email: str | None
    domain_authority: int
    niche: str
    pitch_template_id: str
    expected_anchor: str


BD_MEDIA_SEED = [
    OutreachTarget("techshohor.com",         "editor@techshohor.com",       42, "tech",      "pitch.tech.featured",      "Hypershop BD marketplace"),
    OutreachTarget("nextwebbd.com",          "hello@nextwebbd.com",         38, "tech",      "pitch.tech.partnership",   "Hypershop BD"),
    OutreachTarget("dailytechbd.com",        None,                          35, "tech",      "pitch.tech.review",        "Hypershop electronics"),
    OutreachTarget("prothomalo.com",         "tech@prothomalo.com",         85, "news-tech", "pitch.news.exclusive",     "Hypershop"),
    OutreachTarget("thedailystar.net",       "tech@thedailystar.net",       83, "news-tech", "pitch.news.exclusive",     "Hypershop BD marketplace"),
    OutreachTarget("dhakatribune.com",       "business@dhakatribune.com",   72, "news-biz",  "pitch.news.business",      "Hypershop"),
    OutreachTarget("bproperty.com",          "partnerships@bproperty.com",  58, "lifestyle", "pitch.lifestyle.coupon",   "Hypershop Bangladesh"),
    OutreachTarget("ahmedia.com.bd",         "info@ahmedia.com.bd",         33, "lifestyle", "pitch.lifestyle.gift",     "Hypershop"),
    OutreachTarget("bdjobs.com",             "blog@bdjobs.com",             68, "career",    "pitch.career.guide",       "Hypershop careers"),
    OutreachTarget("therisingbd.com",        "tech@therisingbd.com",        48, "tech",      "pitch.tech.partnership",   "Hypershop"),
    OutreachTarget("priyo.com",              "editor@priyo.com",            55, "news-tech", "pitch.news.brief",         "Hypershop BD"),
    OutreachTarget("bonik.barta.net",        "info@bonik.barta.net",        51, "news-biz",  "pitch.news.business",      "Hypershop e-commerce"),
    OutreachTarget("digitalmarketingbd.com", None,                          26, "marketing", "pitch.marketing.casestudy","Hypershop SEO"),
    OutreachTarget("e-cab.net",              "info@e-cab.net",              31, "ecommerce", "pitch.industry.membership","Hypershop"),
    OutreachTarget("startup-bangladesh.org", "press@startup-bangladesh.org",27, "startup",   "pitch.startup.feature",    "Hypershop founder story"),
]


PITCH_TEMPLATES = {
    "pitch.tech.featured": {
        "subject": "Story idea: How Hypershop hit #1 technical SEO in BD e-commerce",
        "body": (
            "Hi {editor_name},\n\n"
            "We've quietly built Bangladesh's most technically advanced e-commerce platform — 23 JSON-LD schema types, "
            "AMP fallback, dynamic OG with Bangla font, IndexNow live, 56-probe smoke green. Lighthouse 95+ on PDP.\n\n"
            "Would your readers be interested in a teardown of how a BD startup beats Daraz on Core Web Vitals?\n\n"
            "Happy to share data, screenshots, or even open up our admin SEO audit dashboard for a hands-on look.\n\n"
            "— Yousuf, Hypershop"
        ),
    },
    "pitch.news.business": {
        "subject": "Press: Hypershop launches V8 — Bangladesh's first marketplace with 21-state fulfillment FSM",
        "body": (
            "Dear editor,\n\n"
            "Hypershop has launched V8 with full marketplace fulfillment — 11 dispatch tables, 6 BD courier integrations "
            "(Pathao, Steadfast, RedX, Sundarban, Paperfly, eCourier), and the first Mother-QR workflow in BD.\n\n"
            "Quick stats: 12,236 products, 349 R2-hosted hero images, IndexNow live, 220 category FAQs in Bangla.\n\n"
            "Media kit + press release attached.\n\n"
            "— Yousuf, Hypershop"
        ),
    },
    "pitch.lifestyle.coupon": {
        "subject": "Partnership: Exclusive Hypershop coupon for {site_name} readers",
        "body": (
            "Hi {editor_name},\n\n"
            "We'd love to offer your readers an exclusive 15% off coupon (HYPER-{partner_code}) in exchange for a "
            "featured placement on your homepage banner or a sponsored review post.\n\n"
            "Audience overlap looks strong — both target BD urban shoppers 25-40.\n\n"
            "— Yousuf"
        ),
    },
    "pitch.industry.membership": {
        "subject": "e-CAB membership renewal + co-marketing opportunity",
        "body": (
            "Greetings,\n\n"
            "Hypershop is renewing its e-CAB membership and would like to discuss a member-spotlight feature on the "
            "e-CAB blog. We can contribute a guest post on 'Technical SEO for BD marketplaces' that drives links both ways.\n\n"
            "— Yousuf, Founder, Hypershop"
        ),
    },
}


def discover_potential_targets() -> int:
    """Hook for Ahrefs/Moz/SEMrush API integrations (creds-pending).

    Returns count of newly discovered domains queued into seo_backlink_outreach
    with status='discovered'.
    """
    # TODO: integrate Ahrefs API once OPS_AHREFS_API_KEY is provisioned
    return len(BD_MEDIA_SEED)


def render_pitch(template_id: str, **vars: str) -> tuple[str, str]:
    tpl = PITCH_TEMPLATES.get(template_id)
    if not tpl:
        raise KeyError(f"unknown pitch template: {template_id}")
    return (
        tpl["subject"].format(**vars),
        tpl["body"].format(**vars),
    )
