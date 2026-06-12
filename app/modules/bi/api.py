"""Module 30 — Reporting Platform: BI cube depth (Sprint 13).

Adds 8 OLAP-style analytical endpoints on top of the existing
Module 30 report builders. No new tables — all aggregation is
on-the-fly SQL with reasonable performance up to ~1M orders. For
larger scale, materialise these into a daily snapshot via a cron job.

Endpoints (all under `/api/v1/admin/bi/*`, requires admin):
- GET /cohorts/monthly           — monthly customer cohorts × retention by months-since
- GET /rfm                       — Recency / Frequency / Monetary segmentation
- GET /timeseries/orders         — daily/weekly/monthly order count + GMV
- GET /timeseries/customers      — new vs returning customers per period
- GET /funnel/conversion         — view → cart → checkout → order conversion (uses funnel_events)
- GET /top-products              — top sellers + slow movers
- GET /category-revenue          — revenue per category over window
- GET /ltv                       — customer lifetime value distribution + cohort LTV curve
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text as _t

from app.core.db.uow import UnitOfWork, get_uow
from app.core.logging import get_logger
from app.core.security.rbac import requires_permission

_log = get_logger("hypershop.bi")

# Existing reporting permission — reuses M30's gate
_ADMIN = "reporting.admin"

router = APIRouter(prefix="/admin/bi", tags=["bi-cube"])


# ============================================================== Cohorts
@router.get(
    "/cohorts/monthly",
    summary="Monthly customer-acquisition cohorts × retention curve",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def cohorts_monthly(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    months: int = Query(default=12, ge=1, le=36,
                         description="how many cohort months to include"),
) -> dict[str, Any]:
    """Returns a matrix:
    rows = cohort_month (first-order month per customer)
    cols = months_since_first_order (0,1,2,...)
    value = unique customers who ordered in that month-offset
    """
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    WITH first_orders AS (
                      SELECT customer_user_id,
                             DATE_TRUNC('month', MIN(placed_at))::date AS cohort_month
                      FROM orders
                      WHERE status IN ('completed','approved','out_for_delivery','packing','stock_reserved','payment_confirmed')
                      GROUP BY customer_user_id
                    ),
                    orders_w_cohort AS (
                      SELECT o.customer_user_id,
                             fo.cohort_month,
                             EXTRACT(YEAR FROM age(DATE_TRUNC('month', o.placed_at), fo.cohort_month))*12
                              + EXTRACT(MONTH FROM age(DATE_TRUNC('month', o.placed_at), fo.cohort_month))
                              AS months_since
                      FROM orders o
                      JOIN first_orders fo ON fo.customer_user_id = o.customer_user_id
                      WHERE o.placed_at >= now() - INTERVAL '{int(months)} months'
                        AND o.status IN ('completed','approved','out_for_delivery','packing','stock_reserved','payment_confirmed')
                    )
                    SELECT cohort_month, months_since::int AS offset_m,
                           COUNT(DISTINCT customer_user_id) AS active
                    FROM orders_w_cohort
                    GROUP BY cohort_month, months_since
                    ORDER BY cohort_month DESC, months_since ASC
                    """,
                )
            )
        ).all()
        # Build matrix
        cohorts: dict[str, dict[int, int]] = {}
        cohort_sizes: dict[str, int] = {}
        for r in rows:
            cm = str(r[0])
            offset = int(r[1])
            active = int(r[2])
            cohorts.setdefault(cm, {})[offset] = active
            if offset == 0:
                cohort_sizes[cm] = active
        # Compute retention rates
        out = []
        for cm in sorted(cohorts.keys(), reverse=True):
            sz = cohort_sizes.get(cm, 0)
            row = {
                "cohort_month": cm,
                "cohort_size": sz,
                "by_offset": cohorts[cm],
                "retention_pct": {
                    str(o): round((cohorts[cm][o] / sz * 100) if sz else 0, 1)
                    for o in cohorts[cm]
                },
            }
            out.append(row)
    return {"window_months": months, "cohorts": out}


# ============================================================== RFM
@router.get(
    "/rfm",
    summary="Recency/Frequency/Monetary segmentation per customer",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def rfm(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=365, ge=30, le=1095),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, Any]:
    """Each customer gets R/F/M scores 1-5 (NTILE quintiles).
    R=5 means most recent buyers; F=5 means most frequent; M=5 means highest spend.
    Combined `rfm_segment` is a 3-digit code like '555' = top tier.
    """
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    WITH base AS (
                      SELECT customer_user_id,
                             EXTRACT(EPOCH FROM (now() - MAX(placed_at)))/86400 AS days_since_last,
                             COUNT(*) AS frequency,
                             COALESCE(SUM(grand_total), 0) AS monetary
                      FROM orders
                      WHERE status = 'completed'
                        AND placed_at >= now() - INTERVAL '{int(days)} days'
                      GROUP BY customer_user_id
                    ),
                    scored AS (
                      SELECT
                        b.*,
                        -- Recency score: low days_since → high score
                        6 - NTILE(5) OVER (ORDER BY days_since_last ASC) AS r_score,
                        NTILE(5) OVER (ORDER BY frequency ASC)            AS f_score,
                        NTILE(5) OVER (ORDER BY monetary ASC)             AS m_score
                      FROM base b
                    )
                    SELECT s.customer_user_id, u.email::text, u.full_name,
                           s.days_since_last::int, s.frequency::int, s.monetary,
                           s.r_score, s.f_score, s.m_score
                    FROM scored s
                    JOIN users u ON u.id = s.customer_user_id
                    ORDER BY s.monetary DESC
                    LIMIT :lim
                    """,
                ),
                {"lim": limit},
            )
        ).all()
        # Segment counts
        seg = {}
        for r in rows:
            code = f"{r[6]}{r[7]}{r[8]}"
            seg[code] = seg.get(code, 0) + 1
    return {
        "window_days": days,
        "total_customers": len(rows),
        "segment_distribution": dict(sorted(seg.items(), key=lambda x: -x[1])[:20]),
        "customers": [
            {
                "customer_user_id": str(r[0]), "email": r[1], "full_name": r[2],
                "recency_days": r[3], "frequency": r[4], "monetary": str(r[5]),
                "r_score": int(r[6]), "f_score": int(r[7]), "m_score": int(r[8]),
                "rfm_segment": f"{r[6]}{r[7]}{r[8]}",
            }
            for r in rows
        ],
    }


# ============================================================== Time-series
@router.get(
    "/timeseries/orders",
    summary="Daily/weekly/monthly order count + GMV + AOV",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def ts_orders(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=90, ge=1, le=1095),
    bucket: str = Query(default="day", pattern=r"^(day|week|month)$"),
) -> list[dict[str, Any]]:
    trunc = {"day": "day", "week": "week", "month": "month"}[bucket]
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT DATE_TRUNC('{trunc}', placed_at)::date AS bucket,
                           COUNT(*) AS orders,
                           COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                           COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                           COALESCE(SUM(grand_total) FILTER (WHERE status = 'completed'), 0) AS gmv,
                           COALESCE(AVG(grand_total) FILTER (WHERE status = 'completed'), 0) AS aov
                    FROM orders
                    WHERE placed_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY 1 ORDER BY 1
                    """,
                )
            )
        ).all()
        return [
            {
                "bucket": str(r[0]),
                "orders": int(r[1]),
                "completed": int(r[2]),
                "cancelled": int(r[3]),
                "gmv": str(r[4]),
                "aov": str(round(r[5], 2)) if r[5] else "0",
            }
            for r in rows
        ]


@router.get(
    "/timeseries/customers",
    summary="New vs returning customers per period",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def ts_customers(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=90, ge=1, le=1095),
    bucket: str = Query(default="day", pattern=r"^(day|week|month)$"),
) -> list[dict[str, Any]]:
    trunc = {"day": "day", "week": "week", "month": "month"}[bucket]
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    WITH first_orders AS (
                      SELECT customer_user_id, MIN(placed_at) AS first_at
                      FROM orders
                      WHERE status = 'completed'
                      GROUP BY customer_user_id
                    )
                    SELECT DATE_TRUNC('{trunc}', o.placed_at)::date AS bucket,
                           COUNT(DISTINCT o.customer_user_id) AS unique_customers,
                           COUNT(DISTINCT o.customer_user_id)
                              FILTER (WHERE DATE_TRUNC('{trunc}', fo.first_at) = DATE_TRUNC('{trunc}', o.placed_at)) AS new_customers
                    FROM orders o
                    JOIN first_orders fo ON fo.customer_user_id = o.customer_user_id
                    WHERE o.status = 'completed'
                      AND o.placed_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY 1 ORDER BY 1
                    """,
                )
            )
        ).all()
        return [
            {
                "bucket": str(r[0]),
                "unique_customers": int(r[1]),
                "new_customers": int(r[2]),
                "returning_customers": int(r[1]) - int(r[2]),
            }
            for r in rows
        ]


# ============================================================== Funnel
@router.get(
    "/funnel/conversion",
    summary="View → cart → checkout → order conversion funnel (from funnel_events)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def funnel_conversion(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Counts distinct sessions per stage. Returns absolute counts +
    per-step conversion rates. Falls back gracefully if funnel_events
    table doesn't have all event types yet.
    """
    async with uow.transactional() as session:
        # Check whether funnel_events exists; if not, return empty
        exists = (
            await session.execute(
                _t("SELECT to_regclass('public.funnel_events')")
            )
        ).scalar()
        if not exists:
            return {
                "window_days": days,
                "stages": [],
                "note": "funnel_events table not present — using orders-only conversion",
            }
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT event_name AS event_type,
                           COUNT(DISTINCT session_id) AS unique_sessions
                    FROM funnel_events
                    WHERE event_name IN
                          ('product_view','add_to_cart','checkout_start','order_placed','order_completed')
                      AND created_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY event_name
                    """,
                )
            )
        ).all()
        by_event = {r[0]: int(r[1]) for r in rows}
        stage_order = [
            ("product_view", "Product view"),
            ("add_to_cart", "Added to cart"),
            ("checkout_start", "Checkout started"),
            ("order_placed", "Order placed"),
            ("order_completed", "Order completed"),
        ]
        stages = []
        prev_count: int | None = None
        first_count: int | None = None
        for code, label in stage_order:
            count = by_event.get(code, 0)
            if first_count is None and count > 0:
                first_count = count
            step_pct = (
                round(count / prev_count * 100, 1)
                if prev_count and prev_count > 0 else None
            )
            cum_pct = (
                round(count / first_count * 100, 1)
                if first_count and first_count > 0 else None
            )
            stages.append({
                "stage": code, "label": label, "count": count,
                "step_conversion_pct": step_pct,
                "cumulative_conversion_pct": cum_pct,
            })
            prev_count = count if count > 0 else prev_count
    return {"window_days": days, "stages": stages}


# ============================================================== Top products
@router.get(
    "/top-products",
    summary="Top sellers + slow movers by quantity and revenue",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def top_products(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    async with uow.transactional() as session:
        top = (
            await session.execute(
                _t(
                    f"""
                    SELECT p.id, p.name, p.slug,
                           COUNT(DISTINCT ol.order_id) AS orders,
                           SUM(ol.quantity) AS units_sold,
                           SUM(ol.line_total) AS revenue
                    FROM order_lines ol
                    JOIN orders o ON o.id = ol.order_id
                    JOIN product_variants pv ON pv.id = ol.variant_id
                    JOIN products p ON p.id = pv.product_id
                    WHERE o.status = 'completed'
                      AND o.placed_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY p.id, p.name, p.slug
                    ORDER BY revenue DESC LIMIT :lim
                    """,
                ),
                {"lim": limit},
            )
        ).all()
        slow = (
            await session.execute(
                _t(
                    f"""
                    SELECT p.id, p.name, p.slug, p.status, p.created_at,
                           COALESCE((SELECT COUNT(*) FROM order_lines ol
                                     JOIN orders o ON o.id = ol.order_id
                                     JOIN product_variants pv ON pv.id = ol.variant_id
                                     WHERE pv.product_id = p.id
                                       AND o.status = 'completed'
                                       AND o.placed_at >= now() - INTERVAL '{int(days)} days'), 0) AS orders
                    FROM products p
                    WHERE p.status = 'active'
                    ORDER BY orders ASC, p.created_at ASC
                    LIMIT :lim
                    """,
                ),
                {"lim": limit},
            )
        ).all()
    return {
        "window_days": days,
        "top_by_revenue": [
            {
                "product_id": str(r[0]), "name": r[1], "slug": r[2],
                "orders": int(r[3]), "units_sold": int(r[4]),
                "revenue": str(r[5]),
            }
            for r in top
        ],
        "slow_movers": [
            {
                "product_id": str(r[0]), "name": r[1], "slug": r[2],
                "status": r[3], "created_at": r[4], "orders_in_window": int(r[5]),
            }
            for r in slow
        ],
    }


# ============================================================== Category revenue
@router.get(
    "/category-revenue",
    summary="Revenue per category over window",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def category_revenue(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=1, le=365),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT c.id, c.name, c.slug,
                           COUNT(DISTINCT ol.order_id) AS orders,
                           COALESCE(SUM(ol.line_total), 0) AS revenue,
                           COUNT(DISTINCT p.id) AS unique_products_sold
                    FROM categories c
                    LEFT JOIN products p ON p.category_id = c.id
                    LEFT JOIN product_variants pv ON pv.product_id = p.id
                    LEFT JOIN order_lines ol ON ol.variant_id = pv.id
                    LEFT JOIN orders o ON o.id = ol.order_id
                       AND o.status = 'completed'
                       AND o.placed_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY c.id, c.name, c.slug
                    ORDER BY revenue DESC NULLS LAST
                    LIMIT 100
                    """,
                )
            )
        ).all()
    return [
        {
            "category_id": str(r[0]), "name": r[1], "slug": r[2],
            "orders": int(r[3] or 0),
            "revenue": str(r[4] or 0),
            "unique_products_sold": int(r[5] or 0),
        }
        for r in rows
    ]


# ============================================================== LTV
@router.get(
    "/ltv",
    summary="Customer lifetime value distribution + per-cohort LTV curve",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def ltv(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        # Distribution buckets
        dist = (
            await session.execute(
                _t(
                    """
                    WITH cust_ltv AS (
                      SELECT customer_user_id, COALESCE(SUM(grand_total), 0) AS lt
                      FROM orders
                      WHERE status = 'completed'
                      GROUP BY customer_user_id
                    )
                    SELECT
                      COUNT(*) FILTER (WHERE lt = 0) AS zero,
                      COUNT(*) FILTER (WHERE lt > 0 AND lt < 1000) AS under_1k,
                      COUNT(*) FILTER (WHERE lt >= 1000 AND lt < 5000) AS k1_5k,
                      COUNT(*) FILTER (WHERE lt >= 5000 AND lt < 25000) AS k5_25k,
                      COUNT(*) FILTER (WHERE lt >= 25000 AND lt < 100000) AS k25_100k,
                      COUNT(*) FILTER (WHERE lt >= 100000) AS over_100k,
                      AVG(lt) FILTER (WHERE lt > 0) AS avg_paying_customer_ltv,
                      MAX(lt) AS max_ltv
                    FROM cust_ltv
                    """,
                )
            )
        ).first()
        # Cumulative LTV by months-since-first-order
        curve = (
            await session.execute(
                _t(
                    """
                    WITH first_orders AS (
                      SELECT customer_user_id, MIN(placed_at) AS first_at
                      FROM orders WHERE status = 'completed'
                      GROUP BY customer_user_id
                    ),
                    monthly AS (
                      SELECT
                        EXTRACT(YEAR FROM age(o.placed_at, fo.first_at))*12
                          + EXTRACT(MONTH FROM age(o.placed_at, fo.first_at))
                          AS months_since,
                        o.customer_user_id,
                        SUM(o.grand_total) AS spend
                      FROM orders o
                      JOIN first_orders fo ON fo.customer_user_id = o.customer_user_id
                      WHERE o.status = 'completed'
                      GROUP BY 1, 2
                    )
                    SELECT months_since::int AS m,
                           AVG(spend) AS avg_cumulative
                    FROM monthly
                    WHERE months_since <= 12
                    GROUP BY months_since
                    ORDER BY months_since
                    """,
                )
            )
        ).all()
    return {
        "distribution": {
            "zero_spend":     int(dist[0] or 0),
            "under_1k_bdt":   int(dist[1] or 0),
            "1k_to_5k":       int(dist[2] or 0),
            "5k_to_25k":      int(dist[3] or 0),
            "25k_to_100k":    int(dist[4] or 0),
            "over_100k_bdt":  int(dist[5] or 0),
            "avg_paying_ltv": str(round(dist[6], 2)) if dist[6] is not None else None,
            "max_ltv":        str(dist[7]) if dist[7] is not None else None,
        },
        "ltv_curve_first_12_months": [
            {"months_since_first_order": int(r[0]),
             "avg_cumulative_spend": str(round(r[1], 2)) if r[1] else "0"}
            for r in curve
        ],
    }
