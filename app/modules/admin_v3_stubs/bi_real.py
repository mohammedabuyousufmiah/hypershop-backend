"""Real Business-Intelligence computations for the /admin/bi endpoints.

Replaces the hardcoded stub responses with live, defensively-guarded SQL over
the existing ``orders`` / ``sellers`` / ``products`` tables. Every block is
independently try/excepted so a missing table or column degrades to a sane
default (0 / empty) instead of 500-ing the whole call. No migration required.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from app.core.db.uow import UnitOfWork


def _f(v: Any) -> float:
    try:
        return round(float(v), 2)
    except Exception:
        return 0.0


def _pct(now: float, prev: float) -> float:
    if prev <= 0:
        return 0.0
    return round((now - prev) / prev * 100, 1)


async def executive_kpis(uow: UnitOfWork) -> list[dict[str, Any]]:
    """Top-line executive scorecard, 30-day window vs the prior 30 days."""
    async with uow.transactional() as session:

        async def scalar(sql: str, default: object = 0, **p: object) -> object:
            try:
                return (await session.execute(text(sql), p)).scalar_one_or_none() or default
            except Exception:
                return default

        gmv_30 = _f(await scalar(
            "SELECT COALESCE(SUM(grand_total),0) FROM orders "
            "WHERE created_at >= current_date - 29"))
        gmv_prev = _f(await scalar(
            "SELECT COALESCE(SUM(grand_total),0) FROM orders "
            "WHERE created_at >= current_date - 59 AND created_at < current_date - 29"))
        orders_30 = int(await scalar(
            "SELECT count(*) FROM orders WHERE created_at >= current_date - 29"))
        orders_prev = int(await scalar(
            "SELECT count(*) FROM orders "
            "WHERE created_at >= current_date - 59 AND created_at < current_date - 29"))
        buyers_30 = int(await scalar(
            "SELECT count(DISTINCT customer_user_id) FROM orders "
            "WHERE created_at >= current_date - 29"))
        active_sellers = int(await scalar(
            "SELECT count(*) FROM sellers WHERE lower(status) IN "
            "('active','approved','enabled')", default=0))
        active_products = int(await scalar(
            "SELECT count(*) FROM products WHERE lower(status) = 'active'", default=0))
        aov = round(gmv_30 / orders_30, 2) if orders_30 else 0.0
        aov_prev = round(gmv_prev / orders_prev, 2) if orders_prev else 0.0

        return [
            {"metric_code": "gmv_30d", "label": "GMV (30d)", "value": gmv_30,
             "unit": "BDT", "delta_pct": _pct(gmv_30, gmv_prev)},
            {"metric_code": "orders_30d", "label": "Orders (30d)", "value": orders_30,
             "unit": "", "delta_pct": _pct(float(orders_30), float(orders_prev))},
            {"metric_code": "aov_30d", "label": "Avg order value", "value": aov,
             "unit": "BDT", "delta_pct": _pct(aov, aov_prev)},
            {"metric_code": "buyers_30d", "label": "Unique buyers (30d)", "value": buyers_30,
             "unit": "", "delta_pct": 0.0},
            {"metric_code": "active_sellers", "label": "Active sellers", "value": active_sellers,
             "unit": "", "delta_pct": 0.0},
            {"metric_code": "active_products", "label": "Active products", "value": active_products,
             "unit": "", "delta_pct": 0.0},
        ]


# Metrics a sparkline can be requested for → the per-day aggregate SQL fragment.
_SPARK_SQL: dict[str, str] = {
    "gmv": "COALESCE(SUM(grand_total),0)",
    "orders": "count(*)",
    "buyers": "count(DISTINCT customer_user_id)",
}


async def sparkline(uow: UnitOfWork, metric_code: str = "orders", days: int = 14) -> dict[str, Any]:
    """N-day daily series for one metric, with gap-filled zero days."""
    metric_code = metric_code if metric_code in _SPARK_SQL else "orders"
    days = max(2, min(int(days or 14), 90))
    agg = _SPARK_SQL[metric_code]
    async with uow.transactional() as session:
        try:
            raw = list((await session.execute(text(
                f"SELECT created_at::date AS d, {agg} AS v FROM orders "
                f"WHERE created_at >= current_date - :n GROUP BY d ORDER BY d"
            ), {"n": days - 1})).all())
        except Exception:
            raw = []
    by_day = {str(r[0]): _f(r[1]) for r in raw}
    points = []
    for i in range(days - 1, -1, -1):
        d = date.today() - timedelta(days=i)
        points.append({"day": d.strftime("%m-%d"), "value": by_day.get(str(d), 0.0)})
    return {"metric_code": metric_code, "points": points}


async def derived_kpis(uow: UnitOfWork) -> list[dict[str, Any]]:
    """Derived ratios computed across all-time orders."""
    async with uow.transactional() as session:

        async def scalar(sql: str, default: object = 0, **p: object) -> object:
            try:
                return (await session.execute(text(sql), p)).scalar_one_or_none() or default
            except Exception:
                return default

        completion = _f(await scalar(
            "SELECT round(100.0*count(*) FILTER (WHERE status='completed')"
            "/NULLIF(count(*),0),1) FROM orders"))
        cancel_rate = _f(await scalar(
            "SELECT round(100.0*count(*) FILTER (WHERE status IN ('failed','cancelled'))"
            "/NULLIF(count(*),0),1) FROM orders"))
        repeat_rate = _f(await scalar(
            "SELECT round(100.0*count(*) FILTER (WHERE c > 1)/NULLIF(count(*),0),1) "
            "FROM (SELECT customer_user_id, count(*) AS c FROM orders "
            "GROUP BY customer_user_id) t"))
        gmv_per_seller = _f(await scalar(
            "SELECT round(COALESCE(SUM(grand_total),0)/NULLIF((SELECT count(*) FROM sellers "
            "WHERE lower(status) IN ('active','approved','enabled')),0),2) FROM orders"))

        return [
            {"metric_code": "completion_rate", "label": "Order completion rate",
             "value": completion, "unit": "%"},
            {"metric_code": "cancel_rate", "label": "Cancel / fail rate",
             "value": cancel_rate, "unit": "%"},
            {"metric_code": "repeat_buyer_rate", "label": "Repeat-buyer rate",
             "value": repeat_rate, "unit": "%"},
            {"metric_code": "gmv_per_active_seller", "label": "GMV per active seller",
             "value": gmv_per_seller, "unit": "BDT"},
        ]
