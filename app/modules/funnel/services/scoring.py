"""Event scoring + segmentation — pure functions, no DB.

The full ``EVENT_SCORE`` table below catalogues every signal the funnel
COULD score (35 entries — kept for documentation + future phases). For
Phase 1 production we only ACCEPT events on the seven-event allowlist
(``CORE_EVENTS``); ``/events/track`` returns 400 for anything else.

Why the allowlist matters
-------------------------
The standalone funnel docs (``docs/PRODUCTION_GAP.md`` in the source
zip) are blunt about this:

    "Do not start with 50 events. Bad data will destroy your KPI."

If callers fire ``post_like`` without a Meta integration to gate it,
or ``ad_impression`` from every page-view, the conversion-rate
denominators get polluted with junk and every dashboard number stops
meaning what its label says. We open the gate to more events later,
once each upstream emitter is verified — one event at a time.
"""
from __future__ import annotations

# Phase-1 allowlist. Any event_name outside this set is rejected at
# ingest with 400 ``event_not_in_allowlist``. To add an event in
# Phase 2+, add the string here AND confirm the emitter (Meta CAPI,
# storefront, checkout, payment gateway) is wired correctly. Keep
# this list small until each entry is trusted.
CORE_EVENTS: frozenset[str] = frozenset({
    "website_visit",
    "category_view",
    "product_view",
    "add_to_cart",
    "checkout_started",
    "payment_failed",
    "order_confirmed",
})


EVENT_SCORE: dict[str, int] = {
    "ad_impression": 1,
    "video_3s_view": 3,
    "video_25_watch": 8,
    "video_50_watch": 15,
    "video_75_watch": 25,
    "post_like": 5,
    "post_comment": 10,
    "post_share": 15,
    "post_save": 20,
    "profile_visit": 15,
    "product_tag_click": 30,
    "message_click": 40,
    "asked_price": 50,
    "asked_delivery": 55,
    "whatsapp_click": 70,
    "website_click": 60,
    "website_visit": 5,
    "homepage_view": 5,
    "category_view": 10,
    "search_started": 25,
    "banner_click": 15,
    "product_view": 10,
    "product_image_zoom": 15,
    "product_video_watch": 25,
    "review_view": 20,
    "delivery_info_click": 30,
    "wishlist_add": 35,
    "add_to_cart": 60,
    "checkout_started": 80,
    "address_added": 85,
    "payment_method_selected": 90,
    "payment_started": 95,
    "payment_failed": 100,
    "order_confirmed": 120,
    "repeat_purchase": 150,
}

MAX_SCORE_PER_EVENT = 150
MAX_TOTAL_SCORE = 1000


def get_event_score(event_name: str) -> int:
    return min(EVENT_SCORE.get(event_name, 0), MAX_SCORE_PER_EVENT)


def calculate_segment(score: int, last_event_name: str | None = None) -> str:
    if last_event_name == "payment_failed":
        return "Payment Failed Hot Lead"
    if last_event_name == "checkout_started":
        return "Checkout Dropper"
    if last_event_name == "add_to_cart":
        return "Cart Abandoner"
    if last_event_name == "order_confirmed":
        return "Buyer"
    if score >= 150:
        return "VIP / Repeat Buyer"
    if score >= 100:
        return "Buyer or Very Hot Lead"
    if score >= 76:
        return "Hot Lead"
    if score >= 51:
        return "Interested Customer"
    if score >= 21:
        return "Warm Audience"
    return "Cold Visitor"


def recommended_action(segment: str) -> str:
    actions = {
        "Cold Visitor": "Ad retargeting only. No WhatsApp/SMS.",
        "Warm Audience": "Show product benefit and review ads.",
        "Interested Customer": "Retarget with product/category ads.",
        "Hot Lead": "Create follow-up task only if consent exists.",
        "Cart Abandoner": "Cart recovery task if consent exists; otherwise ad retargeting only.",
        "Checkout Dropper": "Customer-care follow-up if consent exists.",
        "Payment Failed Hot Lead": "Payment retry link if consent exists.",
        "Buyer": "Post-order thank-you and upsell only through allowed channels.",
        "Buyer or Very Hot Lead": "Check last event before contacting.",
        "VIP / Repeat Buyer": "VIP retention campaign with consent controls.",
    }
    return actions.get(segment, "Manual review.")
