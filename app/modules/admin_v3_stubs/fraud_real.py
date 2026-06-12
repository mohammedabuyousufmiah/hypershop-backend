"""Real fraud-analytics computations for /admin/fraud-analytics-hardening.

Replaces stub responses with live heuristics over the existing ``orders``
table. No migration: rules are a built-in catalog, velocity/anomalies are
computed on the fly, and ``evaluate`` scores a candidate order (optionally
enriched with the customer's real history). Every query is guarded.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.db.uow import UnitOfWork

# Built-in rule catalog the evaluator applies. Persisting custom rules would
# need a table; these ship as real, documented defaults.
BUILT_IN_RULES: list[dict[str, Any]] = [
    {"id": 1, "code": "high_value_order", "description": "Order value far above the platform AOV",
     "param": "amount_minor", "threshold": 50000_00, "weight": 30, "enabled": True},
    {"id": 2, "code": "cod_high_value", "description": "High-value order paid Cash-on-Delivery",
     "param": "payment_method", "threshold": None, "weight": 25, "enabled": True},
    {"id": 3, "code": "serial_canceller", "description": "Customer cancel/fail rate over 50%",
     "param": "customer_user_id", "threshold": 50, "weight": 35, "enabled": True},
    {"id": 4, "code": "velocity_spike", "description": "More than 5 orders by one customer in 24h",
     "param": "customer_user_id", "threshold": 5, "weight": 20, "enabled": True},
]


def _f(v: Any) -> float:
    try:
        return round(float(v), 2)
    except Exception:
        return 0.0


def list_rules() -> list[dict[str, Any]]:
    return BUILT_IN_RULES


async def velocity(uow: UnitOfWork, limit: int = 20) -> dict[str, Any]:
    """Per-customer order velocity + COD exposure over the last 30 days."""
    limit = max(1, min(int(limit or 20), 100))
    async with uow.transactional() as session:
        try:
            rows = list((await session.execute(text(
                "SELECT customer_user_id, "
                "  count(*) AS orders_30d, "
                "  count(*) FILTER (WHERE created_at >= current_date - 1) AS orders_24h, "
                "  COALESCE(SUM(grand_total),0) AS gmv_30d, "
                "  count(*) FILTER (WHERE lower(coalesce(payment_method,'')) ~ 'cod|cash') AS cod_orders "
                "FROM orders WHERE created_at >= current_date - 29 "
                "GROUP BY customer_user_id "
                "ORDER BY orders_30d DESC, gmv_30d DESC LIMIT :lim"
            ), {"lim": limit})).all())
        except Exception:
            rows = []
    items = [
        {"customer_user_id": str(r[0]), "orders_30d": int(r[1]), "orders_24h": int(r[2]),
         "gmv_30d": _f(r[3]), "cod_orders": int(r[4]),
         "flagged": int(r[2]) > 5}
        for r in rows
    ]
    return {"items": items, "total": len(items)}


async def anomalies(uow: UnitOfWork, limit: int = 30) -> dict[str, Any]:
    """Heuristic-flagged orders: value >= 3x AOV, or serial-canceller buyers."""
    limit = max(1, min(int(limit or 30), 100))
    async with uow.transactional() as session:

        async def scalar(sql: str, default: object = 0, **p: object) -> object:
            try:
                return (await session.execute(text(sql), p)).scalar_one_or_none() or default
            except Exception:
                return default

        async def rows(sql: str, **p: object) -> list:
            try:
                return list((await session.execute(text(sql), p)).all())
            except Exception:
                return []

        aov = _f(await scalar(
            "SELECT COALESCE(AVG(grand_total),0) FROM orders WHERE grand_total > 0"))
        threshold = max(aov * 3, 1)
        high = await rows(
            "SELECT id, customer_user_id, grand_total, status, created_at "
            "FROM orders WHERE grand_total >= :t ORDER BY grand_total DESC LIMIT :lim",
            t=threshold, lim=limit)
        cancellers = await rows(
            "SELECT customer_user_id, "
            "  round(100.0*count(*) FILTER (WHERE status IN ('failed','cancelled'))"
            "/NULLIF(count(*),0),1) AS cancel_rate, count(*) AS total "
            "FROM orders GROUP BY customer_user_id "
            "HAVING count(*) >= 3 AND "
            "  100.0*count(*) FILTER (WHERE status IN ('failed','cancelled'))/count(*) > 50 "
            "ORDER BY cancel_rate DESC LIMIT :lim", lim=limit)

    items: list[dict[str, Any]] = []
    for r in high:
        items.append({
            "type": "high_value_order", "order_id": str(r[0]),
            "customer_user_id": str(r[1]), "value": _f(r[2]), "status": str(r[3]),
            "severity": "high" if _f(r[2]) >= threshold * 2 else "medium",
            "reason": f"Order value {_f(r[2])} >= 3x AOV ({round(threshold, 0)})",
        })
    for r in cancellers:
        items.append({
            "type": "serial_canceller", "customer_user_id": str(r[0]),
            "value": _f(r[1]), "order_count": int(r[2]), "severity": "high",
            "reason": f"Cancel/fail rate {_f(r[1])}% over {int(r[2])} orders",
        })
    return {"items": items, "total": len(items), "aov": aov, "threshold": round(threshold, 0)}


async def evaluate(uow: UnitOfWork, body: dict[str, Any]) -> dict[str, Any]:
    """Score a candidate order against the built-in rules. Uses real customer
    history when ``customer_user_id`` is supplied."""
    score = 0.0
    matched: list[dict[str, Any]] = []
    amount = body.get("amount_minor") or body.get("amount") or 0
    try:
        amount = float(amount)
    except Exception:
        amount = 0.0
    pay = str(body.get("payment_method") or "").lower()
    cust = body.get("customer_user_id")

    if amount >= BUILT_IN_RULES[0]["threshold"]:
        score += BUILT_IN_RULES[0]["weight"]; matched.append(BUILT_IN_RULES[0])
    if ("cod" in pay or "cash" in pay) and amount >= 20000_00:
        score += BUILT_IN_RULES[1]["weight"]; matched.append(BUILT_IN_RULES[1])

    if cust:
        async with uow.transactional() as session:
            try:
                row = (await session.execute(text(
                    "SELECT round(100.0*count(*) FILTER (WHERE status IN "
                    "('failed','cancelled'))/NULLIF(count(*),0),1) AS cr, "
                    "count(*) FILTER (WHERE created_at >= current_date - 1) AS d1 "
                    "FROM orders WHERE customer_user_id = :c"
                ), {"c": cust})).one_or_none()
            except Exception:
                row = None
        if row:
            cr = _f(row[0]); d1 = int(row[1] or 0)
            if cr > BUILT_IN_RULES[2]["threshold"]:
                score += BUILT_IN_RULES[2]["weight"]; matched.append(BUILT_IN_RULES[2])
            if d1 > BUILT_IN_RULES[3]["threshold"]:
                score += BUILT_IN_RULES[3]["weight"]; matched.append(BUILT_IN_RULES[3])

    outcome = "block" if score >= 70 else "review" if score >= 40 else "allow"
    return {"outcome": outcome, "score": round(score, 1),
            "matched_rules": [m["code"] for m in matched]}
