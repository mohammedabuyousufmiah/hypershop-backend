"""Seed 5 universal FAQs per category (EN + BN) → entity_faqs.

Universal Q template covers the BD-purchase intent funnel:
  1. Warranty / authenticity
  2. Delivery time + areas
  3. COD availability
  4. Return / replacement window
  5. EMI / payment options

22 categories × 5 Q × 2 locales = 220 rows. Idempotent: skips
existing (entity_type, entity_key, locale, question) rows.

Run:
    .venv/Scripts/python -m scripts.seo_seed_category_faqs
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


# 5 universal Q × EN+BN. {name} substituted with category display name.
_FAQ_TEMPLATE: list[tuple[str, str, str, str]] = [
    # (question_en, answer_en, question_bn, answer_bn)
    (
        "Are {name} products on Hypershop BD genuine?",
        "Yes. Every {name} item on Hypershop BD is sourced from authorised "
        "brands or licensed distributors. Each product ships with the "
        "official manufacturer warranty (where applicable) and a Hypershop "
        "authenticity tag. Counterfeit or grey-market items are removed "
        "from the catalog within 24 hours of being reported.",
        "Hypershop BD-এ {name} পণ্য কি অরিজিনাল?",
        "জি। Hypershop BD-এর প্রতিটি {name} পণ্য অনুমোদিত ব্র্যান্ড বা "
        "লাইসেন্সড ডিস্ট্রিবিউটর থেকে সংগ্রহ করা। সকল পণ্যে অফিসিয়াল "
        "ম্যানুফ্যাকচারার ওয়ারেন্টি (প্রযোজ্য ক্ষেত্রে) ও Hypershop "
        "অথেনটিসিটি ট্যাগ থাকে। নকল বা গ্রে-মার্কেট পণ্য রিপোর্ট হলে ২৪ "
        "ঘণ্টার মধ্যে ক্যাটালগ থেকে সরানো হয়।",
    ),
    (
        "How long does delivery take for {name} orders?",
        "Inside Dhaka city: 1-2 business days. Outside Dhaka (district HQ): "
        "2-4 business days. Remote sub-districts: 3-7 business days. Express "
        "same-day delivery is available for select {name} products inside "
        "Dhaka if you order before 12:00 PM. You can track your shipment "
        "live from the Hypershop app.",
        "{name} অর্ডার ডেলিভারিতে কতদিন লাগে?",
        "ঢাকা সিটির ভিতরে: ১-২ কর্মদিবস। ঢাকার বাইরে (জেলা সদর): ২-৪ "
        "কর্মদিবস। প্রত্যন্ত উপজেলায়: ৩-৭ কর্মদিবস। ঢাকার ভেতরে দুপুর "
        "১২টার আগে অর্ডার করলে নির্বাচিত {name} পণ্যের জন্য সেইম-ডে "
        "এক্সপ্রেস ডেলিভারি পাওয়া যায়। অ্যাপ থেকে লাইভ শিপমেন্ট ট্র্যাক "
        "করতে পারবেন।",
    ),
    (
        "Is Cash on Delivery (COD) available for {name}?",
        "Yes. Cash on Delivery is available for all {name} products across "
        "every district in Bangladesh. Hand cash to the rider only after "
        "you inspect the package. For orders above ৳ 50,000 a 10% advance "
        "may be requested to confirm the booking — fully refundable if you "
        "cancel before dispatch.",
        "{name} অর্ডারে কি ক্যাশ অন ডেলিভারি (COD) পাওয়া যায়?",
        "জি। বাংলাদেশের প্রতিটি জেলায় {name} পণ্যের জন্য ক্যাশ অন "
        "ডেলিভারি সুবিধা চালু আছে। প্যাকেজ পরীক্ষা করার পরই রাইডারকে "
        "ক্যাশ দিন। ৫০,০০০ টাকার বেশি অর্ডারের জন্য বুকিং নিশ্চিতে ১০% "
        "অ্যাডভান্স চাওয়া হতে পারে — ডিসপ্যাচের আগে বাতিল করলে সম্পূর্ণ "
        "ফেরতযোগ্য।",
    ),
    (
        "What is the return / replacement policy for {name}?",
        "You can request a return or replacement within 7 days of delivery "
        "for any {name} product if the item is defective, damaged in "
        "transit, or doesn't match the listing. Open the order in the app "
        "→ Return → upload a photo → schedule a free pickup. Refund hits "
        "your wallet within 3 business days after warehouse inspection, or "
        "bank/mobile-money within 5-7 days.",
        "{name} পণ্যের রিটার্ন / রিপ্লেসমেন্ট পলিসি কী?",
        "{name} পণ্য ত্রুটিপূর্ণ, ট্রানজিটে ক্ষতিগ্রস্ত বা লিস্টিং-এর "
        "সাথে না মিললে ডেলিভারির ৭ দিনের মধ্যে রিটার্ন/রিপ্লেসমেন্ট "
        "রিকোয়েস্ট করা যায়। অ্যাপ → অর্ডার → Return → ছবি আপলোড → ফ্রি "
        "পিকআপ শিডিউল। ওয়্যারহাউস ইন্সপেকশনের পর ৩ কর্মদিবসে ওয়ালেটে, "
        "বা ৫-৭ দিনে ব্যাংক/মোবাইল মানিতে রিফান্ড আসে।",
    ),
    (
        "Can I pay for {name} with EMI or installments?",
        "Yes. 0% EMI is available on {name} purchases above ৳ 5,000 via "
        "EBL, City Bank, BRAC, DBBL and Mutual Trust credit cards (3 / 6 / "
        "12-month tenors). bKash + Nagad pay-later is available for smaller "
        "tickets. Full EMI breakdown shows on the product page after you "
        "pick your card.",
        "{name} পণ্যে কি EMI বা কিস্তিতে পেমেন্ট করা যায়?",
        "জি। ৫,০০০ টাকার বেশি {name} অর্ডারে EBL, City Bank, BRAC, DBBL ও "
        "Mutual Trust ক্রেডিট কার্ডে ০% EMI পাওয়া যায় (৩ / ৬ / ১২ মাস "
        "মেয়াদ)। ছোট অর্ডারের জন্য bKash + Nagad pay-later উপলব্ধ। কার্ড "
        "সিলেক্ট করলে পণ্য পৃষ্ঠায় সম্পূর্ণ EMI ব্রেকডাউন দেখা যাবে।",
    ),
]


async def main() -> int:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://hypershop:hypershop@localhost:5432/hypershop",
    )
    eng = create_async_engine(url)
    n_inserted = 0
    n_skipped = 0
    async with eng.begin() as c:
        cats = (await c.execute(text(
            "SELECT id::text AS id, slug, name FROM categories ORDER BY name"
        ))).all()
        print(f"found {len(cats)} categories")
        for cat in cats:
            entity_key = cat.id.replace("-", "")  # hex form
            for pos, (qen, aen, qbn, abn) in enumerate(_FAQ_TEMPLATE):
                for locale, q, a in (
                    ("en", qen.format(name=cat.name),
                            aen.format(name=cat.name)),
                    ("bn", qbn.format(name=cat.name),
                            abn.format(name=cat.name)),
                ):
                    # No unique constraint — pre-check existence so the
                    # script stays idempotent across re-runs.
                    exists = (await c.execute(
                        text(
                            "SELECT 1 FROM entity_faqs WHERE "
                            "entity_type='category' AND entity_key = :key "
                            "AND locale = :loc AND question = :q LIMIT 1"
                        ),
                        {"key": entity_key, "loc": locale, "q": q},
                    )).first()
                    if exists is not None:
                        n_skipped += 1
                        continue
                    await c.execute(
                        text(
                            "INSERT INTO entity_faqs ("
                            "  id, entity_type, entity_key, locale,"
                            "  question, answer, position, is_active"
                            ") VALUES ("
                            "  gen_random_uuid(), 'category', :key, :loc,"
                            "  :q, :a, :pos, true"
                            ")"
                        ),
                        {
                            "key": entity_key, "loc": locale,
                            "q": q, "a": a, "pos": pos,
                        },
                    )
                    n_inserted += 1
    await eng.dispose()
    print(f"inserted: {n_inserted}  skipped: {n_skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
