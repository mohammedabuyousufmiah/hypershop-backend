"""Marketing automation service — Module 48.

Two core operations:
1. ``audience_query_sql(rules)`` — turn an audience rule-dict into a
   SQL fragment that selects matching customer user_ids.
2. ``send_campaign(campaign_id)`` — fan out a campaign to its audience
   via the right channel (WhatsApp / SMS / email / in-app). Idempotent
   on (campaign, customer) via a unique constraint on
   ``marketing_campaign_sends``.

The rule schema is intentionally small for v1 — operators compose
audiences from a fixed set of facts. Extending it is a matter of
adding cases to ``audience_query_sql``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text as _t
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

_log = get_logger("hypershop.marketing.service")


# --------------------------------------------------------------- audience eval
def audience_query_sql(rules: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return ``(sql, params)``. SQL selects user_id from a CTE-style
    expression that joins customers + orders + loyalty_accounts +
    cc_customer_profile and applies the filters.

    Always emits a column list of ``(user_id, email, phone, full_name,
    tier, consent_status)`` so the caller can use it directly for
    preview + send dispatch.
    """
    conds: list[str] = []
    params: dict[str, Any] = {}

    # Explicit user-id list — used by the Marketing × Funnel-segment
    # integration to materialise a behavioural cohort into a marketing
    # audience. When ``user_id_in`` is present and non-empty, ALL other
    # rule filters are ignored (the list IS the audience).
    user_id_in = rules.get("user_id_in")
    if user_id_in and isinstance(user_id_in, list) and user_id_in:
        keys: list[str] = []
        for i, uid in enumerate(user_id_in):
            k = f"uid_{i}"
            params[k] = uid
            keys.append(f":{k}")
        sql = (
            "SELECT u.id AS user_id, u.email::text, u.phone, u.full_name, "
            "       COALESCE(la.tier, 'NONE') AS tier, "
            "       COALESCE(p.consent_status, 'allowed') AS consent_status "
            "FROM users u "
            "LEFT JOIN loyalty_accounts la ON la.user_id = u.id "
            "LEFT JOIN cc_customer_profile p ON p.customer_id = u.id "
            f"WHERE u.id IN ({','.join(keys)}) "
            "  AND u.status = 'active'"
        )
        # Still honour the marketing-consent filter (on by default).
        if rules.get("consent_required", True):
            sql += " AND COALESCE(p.consent_status, 'allowed') != 'stopped'"
        return sql, params

    # min_spend / min_orders / max_orders — joined from orders rollup
    has_order_rollup = any(
        k in rules for k in ("min_spend", "min_orders", "max_orders",
                              "last_order_within_days", "no_order_within_days")
    )
    base = (
        "SELECT u.id AS user_id, u.email::text, u.phone, u.full_name, "
        "       COALESCE(la.tier, 'NONE') AS tier, "
        "       COALESCE(p.consent_status, 'allowed') AS consent_status "
        "FROM users u "
        "LEFT JOIN loyalty_accounts la ON la.user_id = u.id "
        "LEFT JOIN cc_customer_profile p ON p.customer_id = u.id "
    )
    if has_order_rollup:
        base += (
            "LEFT JOIN ( "
            "  SELECT customer_user_id, "
            "         COUNT(*) AS order_count, "
            "         COALESCE(SUM(grand_total), 0) AS lifetime_spend, "
            "         MAX(placed_at) AS last_order_at "
            "    FROM orders WHERE status = 'completed' "
            "   GROUP BY customer_user_id "
            ") o ON o.customer_user_id = u.id "
        )
    where = ["u.status = 'active'"]
    if rules.get("min_spend") is not None:
        where.append("COALESCE(o.lifetime_spend, 0) >= :min_spend")
        params["min_spend"] = rules["min_spend"]
    if rules.get("min_orders") is not None:
        where.append("COALESCE(o.order_count, 0) >= :min_orders")
        params["min_orders"] = rules["min_orders"]
    if rules.get("max_orders") is not None:
        where.append("COALESCE(o.order_count, 0) <= :max_orders")
        params["max_orders"] = rules["max_orders"]
    if rules.get("last_order_within_days") is not None:
        d = int(rules["last_order_within_days"])
        where.append(f"o.last_order_at >= now() - INTERVAL '{d} days'")
    if rules.get("no_order_within_days") is not None:
        d = int(rules["no_order_within_days"])
        where.append(
            f"(o.last_order_at IS NULL OR o.last_order_at < now() - INTERVAL '{d} days')"
        )
    tier_in = rules.get("loyalty_tier_in")
    if tier_in and isinstance(tier_in, list) and tier_in:
        # Inline-safe: validate against allowed values
        allowed = {"NONE", "BRONZE", "SILVER", "GOLD", "PLATINUM"}
        clean = [t for t in tier_in if t in allowed]
        if clean:
            where.append("COALESCE(la.tier, 'NONE') IN ("
                         + ",".join(f"'{t}'" for t in clean) + ")")
    # Consent filter on by default (only honoured customers receive marketing)
    if rules.get("consent_required", True):
        where.append("COALESCE(p.consent_status, 'allowed') != 'stopped'")
    sql = base + "WHERE " + " AND ".join(where)
    return sql, params


async def preview_audience(
    session: AsyncSession, rules: dict[str, Any], *, limit_sample: int = 5,
) -> dict[str, Any]:
    """Return ``{count: int, sample: [...]}`` for the rules."""
    sql, params = audience_query_sql(rules)
    cnt_sql = f"SELECT COUNT(*) FROM ({sql}) AS _audience"
    sample_sql = f"{sql} LIMIT :_lim"
    sample_params = {**params, "_lim": limit_sample}
    count = (await session.execute(_t(cnt_sql), params)).scalar_one()
    rows = (await session.execute(_t(sample_sql), sample_params)).all()
    return {
        "count": int(count or 0),
        "sample": [
            {
                "user_id": str(r[0]), "email": r[1], "phone": r[2],
                "full_name": r[3], "tier": r[4], "consent_status": r[5],
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------- send
def _interpolate(template: str, ctx: dict[str, Any]) -> str:
    """Simple {{var}} substitution. Missing keys render as empty."""
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v or ""))
    return out


async def send_campaign(
    session: AsyncSession, *, campaign_id: UUID, batch_limit: int = 200,
) -> dict[str, int]:
    """Dispatch a campaign. Picks all matching audience members up to
    ``batch_limit``, sends per channel, records each in
    marketing_campaign_sends. Idempotent on (campaign, customer)."""
    # Fetch campaign + audience
    camp = (
        await session.execute(
            _t(
                "SELECT c.id, c.audience_id, c.channel, c.template_subject, "
                "       c.template_body, c.whatsapp_template_name, c.status, "
                "       a.rules "
                "FROM marketing_campaigns c JOIN marketing_audiences a "
                "  ON a.id = c.audience_id "
                "WHERE c.id = :c"
            ),
            {"c": campaign_id},
        )
    ).first()
    if camp is None:
        return {"sent": 0, "failed": 0, "skipped": 0, "reason_no_campaign": 1}
    if camp[6] not in ("draft", "scheduled", "sending"):
        return {"sent": 0, "skipped": 1, "reason_wrong_status": 1}
    rules = camp[7] or {}
    # Flip status to sending
    await session.execute(
        _t("UPDATE marketing_campaigns SET status = 'sending', updated_at = now() "
           "WHERE id = :c"),
        {"c": campaign_id},
    )
    # Pull audience batch
    sql, params = audience_query_sql(rules)
    sql += " LIMIT :_lim"
    params["_lim"] = batch_limit
    members = (await session.execute(_t(sql), params)).all()

    sent_count = 0
    failed_count = 0
    skipped_count = 0

    # Inside-the-txn DB writes only — outbound network calls happen
    # AFTER the txn closes (via the caller's outbound dispatcher).
    enqueued: list[dict[str, Any]] = []
    for m in members:
        user_id = m[0]
        email = m[1]
        phone = m[2]
        full_name = m[3]
        # Idempotency: skip if a row already exists for this pair
        dup = (
            await session.execute(
                _t(
                    "SELECT 1 FROM marketing_campaign_sends "
                    "WHERE campaign_id = :c AND customer_user_id = :u LIMIT 1"
                ),
                {"c": campaign_id, "u": user_id},
            )
        ).first()
        if dup:
            skipped_count += 1
            continue
        # Channel-aware contact check
        channel = camp[2]
        if channel in ("whatsapp", "sms") and not phone:
            await session.execute(
                _t(
                    "INSERT INTO marketing_campaign_sends "
                    "(id, campaign_id, customer_user_id, channel, status, error_message) "
                    "VALUES (gen_random_uuid(), :c, :u, :ch, 'skipped_no_contact', "
                    "'no phone on record')"
                ),
                {"c": campaign_id, "u": user_id, "ch": channel},
            )
            skipped_count += 1
            continue
        if channel == "email" and not email:
            await session.execute(
                _t(
                    "INSERT INTO marketing_campaign_sends "
                    "(id, campaign_id, customer_user_id, channel, status, error_message) "
                    "VALUES (gen_random_uuid(), :c, :u, :ch, 'skipped_no_contact', "
                    "'no email on record')"
                ),
                {"c": campaign_id, "u": user_id, "ch": channel},
            )
            skipped_count += 1
            continue
        # Render template
        body = _interpolate(camp[4] or "", {
            "name": full_name or "customer",
            "email": email or "",
            "phone": phone or "",
        })
        subject = _interpolate(camp[3] or "", {
            "name": full_name or "customer",
        })
        # Persist the queued send row INSIDE the txn
        sent_id_row = await session.execute(
            _t(
                "INSERT INTO marketing_campaign_sends "
                "(id, campaign_id, customer_user_id, channel, status) "
                "VALUES (gen_random_uuid(), :c, :u, :ch, 'queued') "
                "RETURNING id"
            ),
            {"c": campaign_id, "u": user_id, "ch": channel},
        )
        send_id = sent_id_row.scalar_one()
        # Stash for post-txn dispatch
        enqueued.append({
            "send_id": send_id, "user_id": user_id, "channel": channel,
            "email": email, "phone": phone,
            "body": body, "subject": subject,
            "whatsapp_template_name": camp[5],
        })
    # Returning the enqueued list lets the route do the network sends
    # AFTER closing the txn.
    return {
        "sent": 0, "failed": 0, "skipped": skipped_count,
        "queued": len(enqueued),
        "_enqueued": enqueued,
        "_status_to_set_after_dispatch": True,
    }


async def mark_send_result(
    session: AsyncSession, *, send_id: UUID,
    ok: bool, provider_message_id: str | None = None,
    error: str | None = None,
) -> None:
    """Update a send row after the outbound network call returns."""
    await session.execute(
        _t(
            "UPDATE marketing_campaign_sends "
            "SET status = :s, sent_at = now(), "
            "    provider_message_id = :pmid, error_message = :em "
            "WHERE id = :sid"
        ),
        {
            "s": "sent" if ok else "failed",
            "pmid": provider_message_id, "em": error, "sid": send_id,
        },
    )


async def finalise_campaign(
    session: AsyncSession, *, campaign_id: UUID,
    sent: int, failed: int,
) -> None:
    await session.execute(
        _t(
            "UPDATE marketing_campaigns "
            "SET status = 'sent', sent_at = now(), "
            "    sent_count = sent_count + :s, "
            "    failed_count = failed_count + :f, "
            "    delivered_count = delivered_count + :s, "
            "    updated_at = now() "
            "WHERE id = :c"
        ),
        {"s": sent, "f": failed, "c": campaign_id},
    )
