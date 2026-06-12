"""6 default templates x 2 channels = 12 entries.

WhatsApp + email share the same message text but WhatsApp variant is
shorter. Lookup key: (milestone, channel)."""
from __future__ import annotations

import re

TEMPLATES: dict[tuple[str, str], dict[str, str]] = {
    ("cart_1h", "whatsapp"): {
        "en_body": "Hi {{customer_name}}, you left {{item_count}} item(s) in your Hypershop cart. Complete checkout in 1 click: {{cart_url}}",
        "bn_body": "হাই {{customer_name}}, আপনার Hypershop কার্টে {{item_count}}টি পণ্য রয়ে গেছে। এক ক্লিকে অর্ডার করুন: {{cart_url}}",
    },
    ("cart_1h", "email"): {
        "en_subject": "You left items in your cart",
        "bn_subject": "আপনার কার্টে পণ্য রয়ে গেছে",
        "en_body": "Hi {{customer_name}},\n\nYou left {{item_count}} item(s) worth ৳{{cart_total}} in your Hypershop cart. Complete checkout now: {{cart_url}}\n\nThe Hypershop team",
        "bn_body": "প্রিয় {{customer_name}},\n\nআপনার Hypershop কার্টে {{item_count}}টি পণ্য (মোট ৳{{cart_total}}) রয়ে গেছে। এখনই অর্ডার করুন: {{cart_url}}\n\nHypershop টিম",
    },
    ("cart_6h", "whatsapp"): {
        "en_body": "{{customer_name}}, your cart is still waiting. Order in the next 6 hours and get free delivery: {{cart_url}}",
        "bn_body": "{{customer_name}}, আপনার কার্ট অপেক্ষা করছে। আগামী ৬ ঘণ্টার মধ্যে অর্ডার করলে ফ্রি ডেলিভারি: {{cart_url}}",
    },
    ("cart_6h", "email"): {
        "en_subject": "Don't forget your cart — free delivery if you order today",
        "bn_subject": "আপনার কার্ট ভুলে যাবেন না — আজই অর্ডার করলে ফ্রি ডেলিভারি",
        "en_body": "Hi {{customer_name}},\n\n{{item_count}} item(s) are still in your cart. Order in the next 6 hours and we'll cover the delivery: {{cart_url}}",
        "bn_body": "প্রিয় {{customer_name}},\n\n{{item_count}}টি পণ্য কার্টে অপেক্ষমাণ। আগামী ৬ ঘণ্টায় অর্ডার করলে ডেলিভারি ফ্রি: {{cart_url}}",
    },
    ("cart_24h", "whatsapp"): {
        "en_body": "Last chance, {{customer_name}}. Your cart expires in 24 hours. Order now: {{cart_url}}",
        "bn_body": "শেষ সুযোগ {{customer_name}}, আপনার কার্ট ২৪ ঘণ্টার মধ্যে মেয়াদ শেষ হবে। অর্ডার করুন: {{cart_url}}",
    },
    ("cart_24h", "email"): {
        "en_subject": "Last call — your cart expires in 24h",
        "bn_subject": "শেষ সুযোগ — আপনার কার্ট ২৪ ঘণ্টায় মেয়াদ শেষ",
        "en_body": "Hi {{customer_name}},\n\nThis is your last reminder — your cart will expire in 24 hours. Order now: {{cart_url}}",
        "bn_body": "প্রিয় {{customer_name}},\n\nএটি শেষ স্মরণিকা — আপনার কার্ট ২৪ ঘণ্টায় মেয়াদ শেষ হবে। অর্ডার করুন: {{cart_url}}",
    },
    ("winback_7d", "whatsapp"): {
        "en_body": "Hey {{customer_name}}, we miss you! Here's ৳100 off on your next Hypershop order: code WB100. Shop now: {{home_url}}",
        "bn_body": "হাই {{customer_name}}, আপনাকে মিস করছি! পরবর্তী Hypershop অর্ডারে ৳১০০ ছাড়: কোড WB100। কেনাকাটা করুন: {{home_url}}",
    },
    ("winback_7d", "email"): {
        "en_subject": "We miss you — here's ৳100 off",
        "bn_subject": "আপনাকে মিস করছি — ৳১০০ ছাড়",
        "en_body": "Hi {{customer_name}},\n\nIt's been a week since your last visit. Here's ৳100 off your next order: use code WB100 at checkout. Shop now: {{home_url}}",
        "bn_body": "প্রিয় {{customer_name}},\n\nএক সপ্তাহ পেরিয়ে গেল। পরবর্তী অর্ডারে ৳১০০ ছাড় পেতে চেকআউটে WB100 কোড ব্যবহার করুন: {{home_url}}",
    },
    ("winback_30d", "whatsapp"): {
        "en_body": "{{customer_name}}, it's been a month! Come back with ৳300 off: code COMEBACK300. {{home_url}}",
        "bn_body": "{{customer_name}}, এক মাস হয়ে গেছে! ৳৩০০ ছাড়ে ফিরে আসুন: কোড COMEBACK300। {{home_url}}",
    },
    ("winback_30d", "email"): {
        "en_subject": "Come back — ৳300 off your next order",
        "bn_subject": "ফিরে আসুন — পরবর্তী অর্ডারে ৳৩০০ ছাড়",
        "en_body": "Hi {{customer_name}},\n\nWe haven't seen you in a month. Here's ৳300 off to make it worth your while: use COMEBACK300 at checkout. {{home_url}}",
        "bn_body": "প্রিয় {{customer_name}},\n\nএক মাস ধরে আপনাকে দেখা যায়নি। ফিরে এলে ৳৩০০ ছাড়: চেকআউটে COMEBACK300। {{home_url}}",
    },
}


def render(milestone: str, channel: str, locale: str, ctx: dict) -> dict:
    """Returns {'subject': str|None, 'body': str, 'template_code': str}."""
    key = (milestone, channel)
    if key not in TEMPLATES:
        key = (milestone, "whatsapp")
    if key not in TEMPLATES:
        raise KeyError(f"No template for {milestone}/{channel}")
    t = TEMPLATES[key]
    body_key = f"{locale}_body" if f"{locale}_body" in t else "en_body"
    subject_key = (
        f"{locale}_subject" if f"{locale}_subject" in t
        else ("en_subject" if "en_subject" in t else None)
    )
    body = _interpolate(t[body_key], ctx)
    subject = _interpolate(t[subject_key], ctx) if subject_key else None
    return {
        "subject": subject,
        "body": body,
        "template_code": f"{milestone}_{channel}_{locale}",
    }


def _interpolate(template: str, ctx: dict) -> str:
    """Mini {{var}} substitution — missing keys render as empty."""
    return re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: str(ctx.get(m.group(1), "")),
        template,
    )
