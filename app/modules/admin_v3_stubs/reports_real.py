"""Real standard-report computations for /admin/reports.

``/executions`` returns a set of live standard reports (sales, sellers,
status mix, payment mix) each computed on the fly from existing tables.
Scheduling policies still need a table, so those stay as stubs.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.db.uow import UnitOfWork


def _f(v: Any) -> float:
    try:
        return round(float(v), 2)
    except Exception:
        return 0.0


async def executions(uow: UnitOfWork) -> dict[str, Any]:
    """Live standard reports, presented as recent 'executions'."""
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

        gmv_30 = _f(await scalar(
            "SELECT COALESCE(SUM(grand_total),0) FROM orders WHERE created_at >= current_date - 29"))
        orders_30 = int(await scalar(
            "SELECT count(*) FROM orders WHERE created_at >= current_date - 29"))
        status_mix = [{"label": str(r[0]), "value": int(r[1])} for r in await rows(
            "SELECT status, count(*) FROM orders GROUP BY status ORDER BY count(*) DESC")]
        payment_mix = [{"label": str(r[0] or "unknown"), "value": int(r[1])} for r in await rows(
            "SELECT payment_method, count(*) FROM orders GROUP BY payment_method "
            "ORDER BY count(*) DESC")]
        top_sellers = [{"label": str(r[0]), "value": _f(r[1])} for r in await rows(
            "SELECT COALESCE(s.shop_name, s.legal_name, 'Seller') AS name, "
            "       COALESCE(SUM(oi.line_total),0) AS g "
            "FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "JOIN sellers s ON s.id = p.seller_id "
            "GROUP BY name ORDER BY g DESC LIMIT 5")]

        items = [
            {"report_code": "sales_30d", "label": "Sales summary (30d)", "status": "completed",
             "summary": {"gmv": gmv_30, "orders": orders_30,
                         "aov": round(gmv_30 / orders_30, 2) if orders_30 else 0.0}},
            {"report_code": "order_status_mix", "label": "Order status mix", "status": "completed",
             "summary": {"breakdown": status_mix}},
            {"report_code": "payment_mix", "label": "Payment-method mix", "status": "completed",
             "summary": {"breakdown": payment_mix}},
            {"report_code": "top_sellers", "label": "Top sellers by GMV", "status": "completed",
             "summary": {"rows": top_sellers}},
        ]
    return {"items": items, "total": len(items)}
