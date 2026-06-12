"""Funnel KPI segmentation deepening — Sprint 15.

Two responsibilities:
1. ``segment_query_sql(rules)`` — turn a behavioural rule-dict into
   SQL selecting matching funnel_customers.
2. Lifecycle stage inference — derive a single-letter stage from
   each customer's recent activity:
       N  = New (created last 7d, no events)
       B  = Browser (events but no add_to_cart)
       C  = Cart abandoner (add_to_cart but no order)
       P  = Purchaser (order in last 30d)
       L  = Lapsed (had orders, none in 90+ days)
       D  = Dormant (no activity in 180+ days)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def segment_query_sql(rules: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return ``(sql, params)``. Output column list:
        funnel_customer_id, hypershop_customer_id, name, email, phone,
        current_score, segment, marketing_consent, last_activity_at
    """
    params: dict[str, Any] = {}
    days = int(rules.get("in_last_days") or 30)
    min_count = int(rules.get("min_event_count") or 1)
    did_events = rules.get("did_events") or []
    did_not_events = rules.get("did_not_events") or []
    score_min = rules.get("score_min")
    score_max = rules.get("score_max")
    category_ids = rules.get("category_id_in") or []
    consent_marketing = rules.get("consent_marketing")
    consent_whatsapp = rules.get("consent_whatsapp")

    # Build the WHERE for the inclusion CTE
    where = [f"created_at >= now() - INTERVAL '{days} days'"]
    if did_events:
        # Inline-safe: validate event names against a known allowlist
        allowed = {
            "product_view", "category_view", "add_to_cart",
            "remove_from_cart", "checkout_start", "checkout_step",
            "order_placed", "order_completed", "order_cancelled",
            "search", "wishlist_add",
        }
        clean = [e for e in did_events if e in allowed]
        if clean:
            where.append("event_name IN (" + ",".join(f"'{e}'" for e in clean) + ")")
    if category_ids:
        # Bind each as a parameter
        cat_keys = []
        for i, cid in enumerate(category_ids):
            k = f"cat_{i}"
            params[k] = cid
            cat_keys.append(f":{k}")
        where.append(f"category_id IN ({','.join(cat_keys)})")

    # Inclusion CTE: customers who DID the listed events
    sql = f"""
    WITH did_events AS (
      SELECT customer_id, COUNT(*) AS evt_count
      FROM funnel_events
      WHERE {' AND '.join(where)}
      GROUP BY customer_id
      HAVING COUNT(*) >= {min_count}
    )
    """

    # Optional exclusion CTE: customers who DID NOT do these events in window
    if did_not_events:
        allowed = {
            "product_view", "category_view", "add_to_cart",
            "remove_from_cart", "checkout_start", "checkout_step",
            "order_placed", "order_completed", "order_cancelled",
            "search", "wishlist_add",
        }
        clean = [e for e in did_not_events if e in allowed]
        if clean:
            sql += f""",
    excluded AS (
      SELECT DISTINCT customer_id
      FROM funnel_events
      WHERE created_at >= now() - INTERVAL '{days} days'
        AND event_name IN ({','.join(f"'{e}'" for e in clean)})
    )"""

    # Final SELECT joining funnel_customers
    consents = []
    if consent_marketing is True:
        consents.append("c.marketing_consent = true")
    if consent_whatsapp is True:
        consents.append("c.whatsapp_consent = true")
    score_conds = []
    if score_min is not None:
        score_conds.append("c.current_score >= :score_min")
        params["score_min"] = score_min
    if score_max is not None:
        score_conds.append("c.current_score <= :score_max")
        params["score_max"] = score_max

    sql += """
    SELECT c.id, c.hypershop_customer_id, c.name, c.email, c.phone,
           c.current_score, c.segment, c.marketing_consent, c.last_activity_at
    FROM did_events de
    JOIN funnel_customers c ON c.id = de.customer_id
    """
    if did_not_events and clean:
        sql += " LEFT JOIN excluded ex ON ex.customer_id = c.id WHERE ex.customer_id IS NULL"
        if consents or score_conds:
            sql += " AND " + " AND ".join(consents + score_conds)
    elif consents or score_conds:
        sql += " WHERE " + " AND ".join(consents + score_conds)
    sql += " ORDER BY c.current_score DESC NULLS LAST"
    return sql, params


async def preview_segment(
    session: AsyncSession, rules: dict[str, Any], *, sample_limit: int = 10,
) -> dict[str, Any]:
    from sqlalchemy import text as _t
    sql, params = segment_query_sql(rules)
    cnt_sql = f"SELECT COUNT(*) FROM ({sql}) _seg"
    sample_sql = f"{sql} LIMIT :_lim"
    sample_params = {**params, "_lim": sample_limit}
    count = (await session.execute(_t(cnt_sql), params)).scalar_one()
    rows = (await session.execute(_t(sample_sql), sample_params)).all()
    return {
        "count": int(count or 0),
        "sample": [
            {
                "id": str(r[0]),
                "hypershop_customer_id": str(r[1]) if r[1] else None,
                "name": r[2], "email": r[3], "phone": r[4],
                "current_score": r[5], "segment": r[6],
                "marketing_consent": r[7], "last_activity_at": r[8],
            }
            for r in rows
        ],
    }
