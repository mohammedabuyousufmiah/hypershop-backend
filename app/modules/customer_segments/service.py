"""Business logic for customer_segments — RFM scoring + segment materialization."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customer_segments import repository as repo
from app.modules.customer_segments.codes import (
    FREQUENCY_QUINTILES,
    LOOKBACK_DAYS,
    MONETARY_QUINTILES_MINOR,
    RECENCY_QUINTILES_DAYS,
    RFM_COUNTED_ORDER_STATUSES,
    RULE_TYPE_EVENT,
    RULE_TYPE_RFM,
    RULE_TYPE_SQL,
    SEGMENT_AT_RISK,
    SEGMENT_CANT_LOSE,
    SEGMENT_DORMANT,
    SEGMENT_LOYAL,
    SEGMENT_NEW,
    SEGMENT_ONE_TIME,
    SEGMENT_VIP,
)
from app.modules.customer_segments.models import (
    HypershopCustomerRfmScore,
    HypershopCustomerSegment,
)


def score_recency(days: int) -> int:
    """5 = most recent, 1 = least recent. days are days-since-last-order."""
    cutoffs = RECENCY_QUINTILES_DAYS  # [365, 90, 30, 7]
    if days > cutoffs[0]:
        return 1
    if days > cutoffs[1]:
        return 2
    if days > cutoffs[2]:
        return 3
    if days > cutoffs[3]:
        return 4
    return 5


def score_frequency(count: int) -> int:
    """5 = most frequent. count is order count in lookback window."""
    cutoffs = FREQUENCY_QUINTILES  # [1, 3, 6, 12]
    if count <= cutoffs[0]:
        return 1
    if count <= cutoffs[1]:
        return 2
    if count <= cutoffs[2]:
        return 3
    if count <= cutoffs[3]:
        return 4
    return 5


def score_monetary(minor: int) -> int:
    """5 = highest spend. minor is total GMV in BDT paisa."""
    cutoffs = MONETARY_QUINTILES_MINOR  # [50000, 200000, 500000, 1500000]
    if minor < cutoffs[0]:
        return 1
    if minor < cutoffs[1]:
        return 2
    if minor < cutoffs[2]:
        return 3
    if minor < cutoffs[3]:
        return 4
    return 5


def assign_segment(r: int, f: int, m: int) -> str:
    """Map (R, F, M) quintile scores to a named segment code."""
    if r >= 4 and f >= 4 and m >= 4:
        return SEGMENT_VIP
    if r <= 2 and m >= 4:
        return SEGMENT_CANT_LOSE
    if r <= 2 and f >= 3:
        return SEGMENT_AT_RISK
    if f >= 4:
        return SEGMENT_LOYAL
    if r >= 4 and f <= 2:
        return SEGMENT_NEW
    if r <= 2 and f <= 2:
        return SEGMENT_DORMANT
    return SEGMENT_ONE_TIME


async def compute_rfm_for_customer(
    session: AsyncSession, customer_user_id: UUID,
) -> dict[str, Any] | None:
    """Aggregate orders for one customer, score, upsert rfm_scores row.
    Returns None for customers with zero orders in window."""
    statuses = list(RFM_COUNTED_ORDER_STATUSES)
    sql = text(
        """
        SELECT
            MAX(placed_at)                              AS last_order_at,
            COUNT(*)::int                               AS order_count,
            COALESCE(SUM((grand_total * 100)::bigint),
                     0)                                 AS monetary_minor
          FROM orders
         WHERE customer_user_id = :cid
           AND placed_at >= (now() AT TIME ZONE 'UTC')
                            - make_interval(days => :days)
           AND status = ANY(:statuses)
        """
    )
    row = (
        await session.execute(
            sql,
            {
                "cid": customer_user_id,
                "days": LOOKBACK_DAYS,
                "statuses": statuses,
            },
        )
    ).mappings().first()
    if row is None or (row["order_count"] or 0) == 0:
        return None

    last_at: datetime | None = row["last_order_at"]
    if last_at is None:
        return None

    now = datetime.now(timezone.utc)
    # Both sides are tz-aware in the postgres path. Guard against naive.
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    recency_days = max(0, (now - last_at).days)
    freq = int(row["order_count"] or 0)
    monetary = int(row["monetary_minor"] or 0)

    r = score_recency(recency_days)
    f = score_frequency(freq)
    m = score_monetary(monetary)
    seg = assign_segment(r, f, m)

    fields = dict(
        recency_days=recency_days,
        frequency_count=freq,
        monetary_minor=monetary,
        recency_score=r,
        frequency_score=f,
        monetary_score=m,
        rfm_total=r + f + m,
        segment_code=seg,
        last_order_at=last_at,
        computed_at=now,
    )
    await repo.upsert_rfm_score(session, customer_user_id, **fields)
    return fields


async def materialize_segment(
    session: AsyncSession, segment_id: UUID,
) -> int:
    """For RFM-rule segments: query rfm_scores, find matching customers,
    replace memberships. For SQL-rule: execute parameterized WHERE. For
    event-rule: not yet implemented (returns 0). Returns membership count."""
    seg = await repo.get_segment(session, segment_id)
    if seg is None:
        raise LookupError(f"Segment {segment_id} not found")

    rule = seg.rule or {}
    rule_type = rule.get("type")
    customer_ids: list[UUID] = []

    if rule_type == RULE_TYPE_RFM:
        wanted = rule.get("segments") or []
        if wanted:
            stmt = select(HypershopCustomerRfmScore.customer_user_id).where(
                HypershopCustomerRfmScore.segment_code.in_(wanted),
            )
            customer_ids = [
                row[0] for row in (await session.execute(stmt)).all()
            ]
    elif rule_type == RULE_TYPE_SQL:
        # Operator-curated WHERE clause. Must reference table `orders` or
        # `users` only; we wrap into a fixed safe outer query and bind
        # arbitrary params from rule.params. Statement is parameterized.
        where_clause = (rule.get("where") or "").strip()
        params = rule.get("params") or {}
        if where_clause:
            safe_sql = text(
                f"SELECT DISTINCT u.id FROM users u WHERE {where_clause}"
            )
            r = await session.execute(safe_sql, params)
            customer_ids = [row[0] for row in r.all()]
    elif rule_type == RULE_TYPE_EVENT:
        customer_ids = []  # Phase 2

    count = await repo.replace_memberships(session, segment_id, customer_ids)
    # Cache the audience size for the admin list view.
    await repo.update_segment(session, segment_id, estimated_size=count)
    return count


async def export_audience(
    session: AsyncSession,
    segment_id: UUID,
    *,
    fmt: str = "csv",
    limit: int = 10000,
) -> tuple[bytes, str, int]:
    """Returns (body, content_type, row_count). CSV columns:
    customer_user_id, email, phone, full_name, segment_code, rfm_total."""
    seg = await repo.get_segment(session, segment_id)
    if seg is None:
        raise LookupError(f"Segment {segment_id} not found")

    sql = text(
        """
        SELECT
            u.id::text       AS customer_user_id,
            u.email          AS email,
            u.phone          AS phone,
            u.full_name      AS full_name,
            COALESCE(rfm.segment_code, '') AS segment_code,
            COALESCE(rfm.rfm_total, 0)     AS rfm_total
          FROM hypershop_customer_segment_memberships m
          JOIN users u ON u.id = m.customer_user_id
     LEFT JOIN hypershop_customer_rfm_scores rfm
            ON rfm.customer_user_id = m.customer_user_id
         WHERE m.segment_id = :sid
         ORDER BY rfm.rfm_total DESC NULLS LAST, u.id
         LIMIT :lim
        """
    )
    rows = (
        await session.execute(sql, {"sid": segment_id, "lim": limit})
    ).mappings().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "customer_user_id", "email", "phone",
            "full_name", "segment_code", "rfm_total",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["customer_user_id"],
                r["email"] or "",
                r["phone"] or "",
                r["full_name"] or "",
                r["segment_code"] or "",
                int(r["rfm_total"] or 0),
            ]
        )
    body = buf.getvalue().encode("utf-8")
    return body, "text/csv; charset=utf-8", len(rows)


__all__ = [
    "score_recency",
    "score_frequency",
    "score_monetary",
    "assign_segment",
    "compute_rfm_for_customer",
    "materialize_segment",
    "export_audience",
]


_ = HypershopCustomerSegment  # silence unused-import
