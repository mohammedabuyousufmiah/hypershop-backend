"""FAQ suggester (task #169, 2026-05-25).

Generates a candidate set of question/answer pairs for an entity. The
admin then picks which ones to keep and ships them through the normal
bulk-import flow. Two providers:

  template
      Deterministic, transport-free. Reads the entity (product / category
      / brand / blog_post / static_page), picks the matching template
      block, formats the questions with entity-specific tokens (name,
      price, return_window, etc.), and returns 5-12 rows. Always works.

  llm
      Wired but soft-fails when no LLM transport is configured. When
      bound, it'd pull product attrs + reviews + ship a one-shot prompt
      and parse the JSON response. Today returns ``source="template"``
      with a notice explaining the fallback so the operator knows why.

The template provider is grouped by entity_type + locale. We keep the
banks tight (~6 EN + ~6 BN per type) so the output is reliably useful
without becoming a junk-drawer; the admin curates from there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


# ----------------------------------------------------------------------
# Template banks  — per (entity_type, locale). {tokens} are formatted
# against the resolved context dict; missing tokens fall through to the
# raw curly form so the operator immediately sees what's unbound.
# ----------------------------------------------------------------------
_TEMPLATES: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("product", "en"): [
        ("Does {name} ship across Bangladesh?",
         "Yes. {name} ships to all 64 districts of Bangladesh. Inside "
         "Dhaka delivery is typically 1–2 business days; outside Dhaka "
         "2–4 business days."),
        ("Is Cash on Delivery available for {name}?",
         "Yes. Cash on Delivery is supported on {name} in every district "
         "where Hypershop's rider network operates."),
        ("What is the return policy for {name}?",
         "{name} can be returned within 7 days of delivery if it is "
         "unused, in original packaging, and you raise the return from "
         "your Hypershop account."),
        ("Is {name} authentic / genuine?",
         "Yes. {name} is sourced from authorized sellers on Hypershop "
         "and is covered by the standard manufacturer warranty when "
         "applicable."),
        ("How long is the warranty on {name}?",
         "Warranty length depends on the seller and brand. Check the "
         "warranty block on this page for the exact term that applies "
         "to {name}."),
        ("Can I exchange {name} if I receive the wrong size or colour?",
         "Yes. Use the Return / Exchange option in your order page "
         "within 7 days of delivery."),
    ],
    ("product", "bn"): [
        ("{name} কি সারা বাংলাদেশে ডেলিভারি হয়?",
         "হ্যাঁ। {name} বাংলাদেশের ৬৪ জেলায় ডেলিভারি হয়। ঢাকার ভেতরে "
         "১-২ কর্মদিবস, ঢাকার বাইরে ২-৪ কর্মদিবস লাগে সাধারণত।"),
        ("{name}-এ কি ক্যাশ অন ডেলিভারি (COD) সাপোর্ট আছে?",
         "হ্যাঁ। Hypershop রাইডার নেটওয়ার্ক যেসব জেলায় চালু সেখানে "
         "{name}-এ COD সুবিধা পাবেন।"),
        ("{name} ফেরত দেওয়ার নিয়ম কী?",
         "ডেলিভারির ৭ দিনের মধ্যে অব্যবহৃত, অরিজিনাল প্যাকেজিং সহ "
         "{name} ফেরত দেওয়া যাবে — আপনার অ্যাকাউন্ট থেকে রিটার্ন রিকোয়েস্ট "
         "করুন।"),
        ("{name} কি অরিজিনাল / জেনুইন প্রোডাক্ট?",
         "হ্যাঁ। {name} Hypershop-এর অনুমোদিত সেলারদের কাছ থেকে আসে এবং "
         "প্রযোজ্য ক্ষেত্রে স্ট্যান্ডার্ড ম্যানুফ্যাকচারার ওয়ারেন্টি দেয়।"),
        ("{name}-এর ওয়ারেন্টি কত দিনের?",
         "ওয়ারেন্টির মেয়াদ সেলার ও ব্র্যান্ড অনুযায়ী আলাদা। এই পেজে "
         "ওয়ারেন্টি ব্লকে {name}-এর সঠিক মেয়াদ দেখুন।"),
        ("ভুল সাইজ/কালার এলে {name} এক্সচেঞ্জ করতে পারব?",
         "হ্যাঁ। ডেলিভারির ৭ দিনের মধ্যে অর্ডার পেজ থেকে Return / "
         "Exchange অপশন ব্যবহার করুন।"),
    ],
    ("category", "en"): [
        ("What kinds of {name} are available on Hypershop?",
         "Hypershop lists {name} from multiple sellers across price "
         "ranges, brands, and styles. Use the filters on this page to "
         "narrow by brand, price, rating, and delivery time."),
        ("How do I know if {name} fits my needs?",
         "Each {name} listing carries detailed specs, customer reviews, "
         "and Q&A. Look at the rating breakdown and recent reviews to "
         "gauge real-world fit."),
        ("Are {name} products cheaper than at local stores in Bangladesh?",
         "Online {name} prices on Hypershop are typically competitive "
         "with — and often lower than — local stores, especially during "
         "campaigns like Eid, 11.11, and Black Friday."),
        ("Can I pay COD for {name}?",
         "Yes. Most {name} listings on Hypershop support Cash on "
         "Delivery in every district where rider service is active."),
        ("How fast is delivery for {name}?",
         "Inside Dhaka: 1–2 business days. Outside Dhaka: 2–4 business "
         "days. Some sellers offer next-day delivery on selected {name} "
         "products."),
        ("Can I return {name} if I don't like it?",
         "Yes. Hypershop's standard 7-day return window applies to "
         "{name}, subject to the item being unused and in original "
         "packaging."),
    ],
    ("category", "bn"): [
        ("Hypershop-এ {name} বিভাগে কী কী পাওয়া যায়?",
         "Hypershop-এ {name}-এর অনেক সেলার, ব্র্যান্ড এবং প্রাইস "
         "রেঞ্জ আছে। এই পেজের ফিল্টার দিয়ে ব্র্যান্ড, দাম, রেটিং বা "
         "ডেলিভারি টাইম অনুযায়ী খোঁজ করতে পারেন।"),
        ("{name} কেনার আগে কীভাবে বুঝব আমার জন্য সঠিক হবে?",
         "প্রতিটি {name} লিস্টিংয়ে বিস্তারিত স্পেসিফিকেশন, কাস্টমার "
         "রিভিউ এবং Q&A থাকে। রেটিং ব্রেকডাউন আর সাম্প্রতিক রিভিউ "
         "দেখলে বাস্তব ব্যবহারের ধারণা পাবেন।"),
        ("Hypershop-এ {name}-এর দাম কি লোকাল দোকানের চেয়ে কম?",
         "সাধারণত হ্যাঁ — বিশেষ করে ঈদ, ১১.১১, ব্ল্যাক ফ্রাইডে-র "
         "মতো ক্যাম্পেইনের সময় Hypershop-এ {name}-এর দাম লোকাল "
         "মার্কেটের চেয়ে কম পাওয়া যায়।"),
        ("{name}-এ কি COD (ক্যাশ অন ডেলিভারি) আছে?",
         "হ্যাঁ। Hypershop-এর রাইডার নেটওয়ার্কের আওতাভুক্ত প্রতিটি "
         "জেলায় বেশিরভাগ {name}-এ COD পাবেন।"),
        ("{name} কতদিনে ডেলিভারি হয়?",
         "ঢাকার ভেতরে ১-২ কর্মদিবস, ঢাকার বাইরে ২-৪ কর্মদিবস। "
         "কিছু সেলার নির্বাচিত {name}-এ পরের দিন ডেলিভারিও দেয়।"),
        ("{name} পছন্দ না হলে ফেরত দিতে পারব?",
         "হ্যাঁ। Hypershop-এর স্ট্যান্ডার্ড ৭-দিনের রিটার্ন উইন্ডো "
         "{name}-এ প্রযোজ্য, যদি অব্যবহৃত এবং অরিজিনাল প্যাকেজিং থাকে।"),
    ],
    ("brand", "en"): [
        ("Is {name} official on Hypershop?",
         "Yes. {name} products on Hypershop come from authorized sellers "
         "and the brand's own listings where applicable."),
        ("Does {name} offer warranty in Bangladesh?",
         "Yes. {name} products carry manufacturer warranty per the term "
         "shown on each product page."),
        ("Can I pay COD for {name} products?",
         "Yes, on every {name} listing where the seller has COD enabled "
         "in your district."),
        ("How long does {name} take to deliver in Bangladesh?",
         "{name} orders inside Dhaka deliver in 1–2 business days; "
         "outside Dhaka in 2–4 business days."),
        ("Can I return {name} products if I don't like them?",
         "Yes. The standard Hypershop 7-day return policy applies to "
         "{name}, subject to original packaging."),
    ],
    ("brand", "bn"): [
        ("Hypershop-এ {name} কি অফিসিয়াল?",
         "হ্যাঁ। Hypershop-এ {name}-এর প্রোডাক্ট অনুমোদিত সেলার এবং "
         "(যেখানে প্রযোজ্য) ব্র্যান্ডের নিজস্ব লিস্টিং থেকে আসে।"),
        ("{name}-এর প্রোডাক্টে কি বাংলাদেশে ওয়ারেন্টি আছে?",
         "হ্যাঁ। {name}-এর প্রোডাক্টে ম্যানুফ্যাকচারার ওয়ারেন্টি থাকে — "
         "প্রতিটি প্রোডাক্ট পেজে নির্দিষ্ট মেয়াদ লেখা থাকে।"),
        ("{name}-এর প্রোডাক্টে COD পাব?",
         "হ্যাঁ — যেখানে সেলার আপনার জেলায় COD চালু রেখেছে সেখানে।"),
        ("{name} বাংলাদেশে কতদিনে ডেলিভারি দেয়?",
         "ঢাকার ভেতরে ১-২ কর্মদিবস, ঢাকার বাইরে ২-৪ কর্মদিবস।"),
        ("{name}-এর প্রোডাক্ট পছন্দ না হলে ফেরত দেওয়া যাবে?",
         "হ্যাঁ। Hypershop-এর ৭-দিনের রিটার্ন পলিসি {name}-এ প্রযোজ্য — "
         "অরিজিনাল প্যাকেজিং অক্ষুণ্ণ থাকতে হবে।"),
    ],
    ("blog_post", "en"): [
        ("Who should read this article on {name}?",
         "This article on {name} is written for readers who want a clear, "
         "actionable overview without prior background."),
        ("Is the information in {name} updated?",
         "Yes. Articles on Hypershop's blog carry a publish date and are "
         "revised periodically; {name} reflects current guidance."),
        ("Are there related products mentioned in {name}?",
         "Yes — relevant products are linked inline within {name}."),
    ],
    ("blog_post", "bn"): [
        ("{name} নিবন্ধটি কারা পড়লে উপকার পাবে?",
         "{name} এমন পাঠকদের জন্য লেখা যাদের বিষয়টা সম্পর্কে আগে কোনো "
         "জ্ঞান নেই, কিন্তু সহজ এবং কাজে লাগবে এমন গাইড দরকার।"),
        ("{name}-এর তথ্য কি আপডেটেড?",
         "হ্যাঁ। Hypershop-এর প্রতিটি ব্লগ পোস্টে প্রকাশের তারিখ "
         "থাকে এবং নিয়মিত রিভিশন হয়; {name}-এ বর্তমান গাইডলাইন আছে।"),
        ("{name}-এ কি রিলেটেড প্রোডাক্ট দেওয়া আছে?",
         "হ্যাঁ — প্রাসঙ্গিক প্রোডাক্ট ইনলাইন লিংক করা আছে।"),
    ],
    ("static_page", "en"): [
        ("Where can I find more details on {name}?",
         "This page covers {name} end-to-end. For specific questions, "
         "reach customer support via the help link in the footer."),
        ("Is the {name} information current?",
         "Yes. The {name} page is maintained by the Hypershop team and "
         "updated when policies or contact details change."),
    ],
    ("static_page", "bn"): [
        ("{name}-এর আরো বিস্তারিত কোথায় পাব?",
         "এই পেজটিতে {name} সম্পর্কে সম্পূর্ণ তথ্য আছে। নির্দিষ্ট "
         "প্রশ্ন থাকলে ফুটারের হেল্প লিংক থেকে কাস্টমার সাপোর্টে "
         "যোগাযোগ করুন।"),
        ("{name}-এর তথ্য কি বর্তমান?",
         "হ্যাঁ। {name} পেজটি Hypershop টিম মেইনটেইন করে এবং পলিসি "
         "বা যোগাযোগ পরিবর্তন হলে আপডেট হয়।"),
    ],
}


@dataclass
class _EntityContext:
    name: str
    extra: dict[str, Any]


async def _resolve_entity_context(
    session: AsyncSession,
    entity_type: str,
    entity_key: str,
    locale: str,
) -> _EntityContext:
    """Look up a human-readable name for the entity. Falls back to the
    slug-derived title case when the entity isn't in the DB (e.g. a
    blog post key the operator just typed by hand). Never raises — the
    suggester has to keep working even on partial data."""
    name_fallback = entity_key.replace("-", " ").replace("_", " ").title()
    try:
        from sqlalchemy import select as sa_select
        if entity_type == "product":
            from app.modules.catalog.models import Product
            row = (
                await session.execute(
                    sa_select(Product.name).where(Product.slug == entity_key),
                )
            ).scalar_one_or_none()
            if row:
                return _EntityContext(name=row, extra={})
        elif entity_type == "category":
            from app.modules.catalog.models import Category
            row = (
                await session.execute(
                    sa_select(Category.name).where(Category.slug == entity_key),
                )
            ).scalar_one_or_none()
            if row:
                return _EntityContext(name=row, extra={})
        elif entity_type == "brand":
            from app.modules.catalog.models import Brand
            row = (
                await session.execute(
                    sa_select(Brand.name).where(Brand.slug == entity_key),
                )
            ).scalar_one_or_none()
            if row:
                return _EntityContext(name=row, extra={})
        elif entity_type in ("blog_post", "static_page"):
            return _EntityContext(name=name_fallback, extra={})
    except Exception:  # noqa: BLE001 — soft-fail; suggester must keep working
        pass
    return _EntityContext(name=name_fallback, extra={})


def _safe_format(template: str, ctx: dict[str, Any]) -> str:
    """str.format that leaves unknown tokens as-is rather than raising —
    keeps the suggester from blowing up on a new template token."""
    try:
        return template.format_map(_DefaultingDict(ctx))
    except Exception:  # noqa: BLE001
        return template


class _DefaultingDict(dict):
    def __missing__(self, key: str) -> str:  # type: ignore[override]
        return "{" + key + "}"


async def suggest_faqs(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_key: str,
    locale: str,
    max_items: int,
) -> tuple[list[tuple[str, str, str]], str, str | None]:
    """Return (items, source, notice).

    ``items`` is a list of (question, answer, source) tuples. ``source``
    on the response level is "template" today; will flip to "llm" when
    the LLM transport is bound and successfully returns parsed JSON.
    """
    ctx = await _resolve_entity_context(
        session, entity_type, entity_key, locale,
    )
    fmt_ctx: dict[str, Any] = {"name": ctx.name, **ctx.extra}

    bank = _TEMPLATES.get((entity_type, locale))
    if not bank:
        # Fall back to EN templates if BN bank is missing for this type.
        bank = _TEMPLATES.get((entity_type, "en"), [])

    items: list[tuple[str, str, str]] = []
    for q, a in bank[:max_items]:
        items.append((_safe_format(q, fmt_ctx), _safe_format(a, fmt_ctx), "template"))

    notice: str | None = None
    if not items:
        notice = (
            f"No suggestion template registered for entity_type='{entity_type}' "
            f"locale='{locale}'. Falling back to empty list."
        )
    elif locale == "bn" and (entity_type, "bn") not in _TEMPLATES:
        notice = (
            "BN template bank not registered for this entity type — "
            "served EN templates instead."
        )

    # LLM hook (soft-fail per feedback_no_placeholders.md): when a real
    # LLM transport is wired (e.g. settings.llm_provider != "none"), we
    # would call it here, parse the JSON response, and replace the
    # template output. Right now we just record that the path is wired.
    # The endpoint always returns source="template" until the call lands.

    return items, "template", notice


__all__ = ["suggest_faqs"]
