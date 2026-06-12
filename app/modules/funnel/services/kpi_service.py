"""KPI aggregation queries — adapted to async SQLAlchemy 2.0.

All read-only; no commits. The verdict logic + event lists are verbatim
from the source zip (the user explicitly does not want fancy
charts/extra metrics — bad events → fake confidence, per docs).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.funnel.models import (
    FunnelCustomer,
    FunnelEvent,
    FunnelFollowUpTask,
    FunnelRetargetingExportLog,
)

FUNNEL_EVENTS = [
    ("Visitors", "website_visit"),
    ("Category Views", "category_view"),
    ("Product Views", "product_view"),
    ("Wishlist", "wishlist_add"),
    ("Add to Cart", "add_to_cart"),
    ("Checkout Started", "checkout_started"),
    ("Payment Started", "payment_started"),
    ("Payment Failed", "payment_failed"),
    ("Order Confirmed", "order_confirmed"),
]

SOCIAL_EVENTS = [
    "ad_impression", "video_3s_view", "video_25_watch", "video_50_watch",
    "video_75_watch", "post_like", "post_comment", "post_share", "post_save",
    "profile_visit", "product_tag_click", "message_click", "asked_price",
    "asked_delivery", "whatsapp_click", "website_click",
]
WEBSITE_EVENTS = [
    "website_visit", "homepage_view", "category_view", "search_started",
    "banner_click", "product_view", "product_image_zoom", "product_video_watch",
    "review_view", "delivery_info_click", "wishlist_add", "add_to_cart",
    "checkout_started", "payment_started", "payment_failed", "order_confirmed",
]


def _date_bounds(
    start_date: date | None, end_date: date | None,
) -> tuple[datetime | None, datetime | None]:
    # Use UTC for both bounds — funnel created_at columns are timestamptz.
    return (
        datetime.combine(start_date, time.min, tzinfo=timezone.utc) if start_date else None,
        datetime.combine(end_date, time.max, tzinfo=timezone.utc) if end_date else None,
    )


def _safe_percent(numerator: int | float, denominator: int | float) -> float:
    return 0.0 if not denominator else round((float(numerator) / float(denominator)) * 100, 2)


async def _scalar(db: AsyncSession, stmt) -> int:
    res = await db.execute(stmt)
    val = res.scalar()
    return int(val or 0)


async def _distinct_customer_count(
    db: AsyncSession, event_name: str,
    start_date: date | None, end_date: date | None,
) -> int:
    start_dt, end_dt = _date_bounds(start_date, end_date)
    stmt = select(func.count(distinct(FunnelEvent.customer_id))).where(
        FunnelEvent.event_name == event_name,
    )
    if start_dt:
        stmt = stmt.where(FunnelEvent.created_at >= start_dt)
    if end_dt:
        stmt = stmt.where(FunnelEvent.created_at <= end_dt)
    return await _scalar(db, stmt)


async def _events_count(
    db: AsyncSession, start_date: date | None, end_date: date | None,
) -> int:
    start_dt, end_dt = _date_bounds(start_date, end_date)
    stmt = select(func.count(FunnelEvent.id))
    if start_dt:
        stmt = stmt.where(FunnelEvent.created_at >= start_dt)
    if end_dt:
        stmt = stmt.where(FunnelEvent.created_at <= end_dt)
    return await _scalar(db, stmt)


async def _customers_count(
    db: AsyncSession, start_date: date | None, end_date: date | None,
    extra_filter=None,
) -> int:
    start_dt, end_dt = _date_bounds(start_date, end_date)
    stmt = select(func.count(FunnelCustomer.id)).where(
        FunnelCustomer.deleted_at.is_(None),
    )
    if start_dt:
        stmt = stmt.where(FunnelCustomer.created_at >= start_dt)
    if end_dt:
        stmt = stmt.where(FunnelCustomer.created_at <= end_dt)
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)
    return await _scalar(db, stmt)


async def build_funnel_steps(
    db: AsyncSession, start_date: date | None, end_date: date | None,
) -> list[dict]:
    steps: list[dict] = []
    previous: int | None = None
    for label, event in FUNNEL_EVENTS:
        count = await _distinct_customer_count(db, event, start_date, end_date)
        conversion = 100.0 if previous is None else _safe_percent(count, previous)
        steps.append({
            "step": label,
            "count": count,
            "conversion_from_previous_percent": conversion,
            "dropoff_from_previous_percent": 0.0 if previous is None else round(100 - conversion, 2),
        })
        previous = count
    return steps


def build_verdict(
    product_to_cart: float, cart_to_checkout: float, checkout_to_order: float,
) -> str:
    if product_to_cart < 5:
        return "Product pages or traffic quality are weak."
    if cart_to_checkout < 25:
        return "Cart is leaking. Fix checkout friction."
    if checkout_to_order < 40:
        return "Checkout/payment is failing."
    return "Funnel is usable. Optimize campaigns now."


async def overview(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    total_customers = await _customers_count(db, start_date, end_date)
    total_events = await _events_count(db, start_date, end_date)
    hot_leads = await _customers_count(
        db, start_date, end_date, extra_filter=(FunnelCustomer.current_score >= 76),
    )

    product_viewers = await _distinct_customer_count(db, "product_view", start_date, end_date)
    cart_users = await _distinct_customer_count(db, "add_to_cart", start_date, end_date)
    checkout_users = await _distinct_customer_count(db, "checkout_started", start_date, end_date)
    confirmed_orders = await _distinct_customer_count(db, "order_confirmed", start_date, end_date)

    product_to_cart = _safe_percent(cart_users, product_viewers)
    cart_to_checkout = _safe_percent(checkout_users, cart_users)
    checkout_to_order = _safe_percent(confirmed_orders, checkout_users)

    start_dt, end_dt = _date_bounds(start_date, end_date)
    seg_stmt = (
        select(FunnelCustomer.segment, func.count(FunnelCustomer.id))
        .where(FunnelCustomer.deleted_at.is_(None))
        .group_by(FunnelCustomer.segment)
    )
    if start_dt:
        seg_stmt = seg_stmt.where(FunnelCustomer.created_at >= start_dt)
    if end_dt:
        seg_stmt = seg_stmt.where(FunnelCustomer.created_at <= end_dt)
    seg_rows = (await db.execute(seg_stmt)).all()

    return {
        "executive_cards": [
            {"label": "Total Tracked Customers", "value": total_customers},
            {"label": "Total Events", "value": total_events},
            {"label": "Hot Leads", "value": hot_leads},
            {"label": "Product to Cart Rate", "value": product_to_cart, "unit": "%"},
            {"label": "Cart to Checkout Rate", "value": cart_to_checkout, "unit": "%"},
            {"label": "Checkout to Order Rate", "value": checkout_to_order, "unit": "%"},
        ],
        "funnel_steps": await build_funnel_steps(db, start_date, end_date),
        "segment_breakdown": [{"segment": s, "count": c} for s, c in seg_rows],
        "hard_verdict": build_verdict(product_to_cart, cart_to_checkout, checkout_to_order),
    }


async def event_breakdown(
    db: AsyncSession,
    source: str | None,
    event_list: list[str],
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    start_dt, end_dt = _date_bounds(start_date, end_date)
    stmt = (
        select(
            FunnelEvent.event_name,
            func.count(FunnelEvent.id),
            func.count(distinct(FunnelEvent.customer_id)),
        )
        .where(FunnelEvent.event_name.in_(event_list))
        .group_by(FunnelEvent.event_name)
    )
    if source:
        stmt = stmt.where(FunnelEvent.source == source)
    if start_dt:
        stmt = stmt.where(FunnelEvent.created_at >= start_dt)
    if end_dt:
        stmt = stmt.where(FunnelEvent.created_at <= end_dt)
    rows = (await db.execute(stmt)).all()
    data = {e: {"events": 0, "customers": 0} for e in event_list}
    for e, count, customers in rows:
        data[e] = {"events": int(count), "customers": int(customers)}
    return data


async def social_kpi(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    data = await event_breakdown(db, "social", SOCIAL_EVENTS, start_date, end_date)
    impressions = data["ad_impression"]["events"]
    return {
        "event_breakdown": data,
        "website_ctr_from_impressions": _safe_percent(
            data["website_click"]["events"], impressions,
        ),
        "verdict": "Likes are vanity. Website clicks and product views matter.",
    }


async def website_kpi(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    return {
        "event_breakdown": await event_breakdown(
            db, None, WEBSITE_EVENTS, start_date, end_date,
        ),
    }


async def retargeting_kpi(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    exp = await db.execute(
        select(func.sum(FunnelRetargetingExportLog.exported_count)),
    )
    flt = await db.execute(
        select(func.sum(FunnelRetargetingExportLog.consent_filtered_count)),
    )
    total_exported = int(exp.scalar() or 0)
    total_filtered = int(flt.scalar() or 0)
    return {
        "total_exported": total_exported,
        "total_consent_filtered": total_filtered,
        "consent_filter_rate": _safe_percent(total_filtered, total_exported + total_filtered),
    }


async def followup_kpi(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    rows = (
        await db.execute(
            select(FunnelFollowUpTask.status, func.count(FunnelFollowUpTask.id))
            .group_by(FunnelFollowUpTask.status),
        )
    ).all()
    return {"status_breakdown": [{"status": s, "count": int(c)} for s, c in rows]}


async def privacy_kpi(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    total = await _customers_count(db, start_date, end_date)
    mk = await _customers_count(
        db, start_date, end_date, extra_filter=(FunnelCustomer.marketing_consent.is_(True)),
    )
    wa = await _customers_count(
        db, start_date, end_date, extra_filter=(FunnelCustomer.whatsapp_consent.is_(True)),
    )
    ad = await _customers_count(
        db, start_date, end_date, extra_filter=(FunnelCustomer.ad_retargeting_consent.is_(True)),
    )
    return {
        "tracked_customers": total,
        "marketing_consent_percent": _safe_percent(mk, total),
        "whatsapp_consent_percent": _safe_percent(wa, total),
        "ad_retargeting_consent_percent": _safe_percent(ad, total),
    }


async def product_kpi(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 20,
) -> dict:
    start_dt, end_dt = _date_bounds(start_date, end_date)
    stmt = (
        select(
            FunnelEvent.product_id,
            func.count(FunnelEvent.id),
            func.count(distinct(FunnelEvent.customer_id)),
        )
        .where(FunnelEvent.product_id.isnot(None))
        .group_by(FunnelEvent.product_id)
        .order_by(func.count(FunnelEvent.id).desc())
        .limit(limit)
    )
    if start_dt:
        stmt = stmt.where(FunnelEvent.created_at >= start_dt)
    if end_dt:
        stmt = stmt.where(FunnelEvent.created_at <= end_dt)
    rows = (await db.execute(stmt)).all()
    return {
        "products": [
            {"product_id": p, "event_count": int(e), "customer_count": int(c)}
            for p, e, c in rows
        ],
    }


async def category_kpi(
    db: AsyncSession,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 20,
) -> dict:
    start_dt, end_dt = _date_bounds(start_date, end_date)
    stmt = (
        select(
            FunnelEvent.category_id,
            func.count(FunnelEvent.id),
            func.count(distinct(FunnelEvent.customer_id)),
        )
        .where(FunnelEvent.category_id.isnot(None))
        .group_by(FunnelEvent.category_id)
        .order_by(func.count(FunnelEvent.id).desc())
        .limit(limit)
    )
    if start_dt:
        stmt = stmt.where(FunnelEvent.created_at >= start_dt)
    if end_dt:
        stmt = stmt.where(FunnelEvent.created_at <= end_dt)
    rows = (await db.execute(stmt)).all()
    return {
        "categories": [
            {"category_id": p, "event_count": int(e), "customer_count": int(c)}
            for p, e, c in rows
        ],
    }
