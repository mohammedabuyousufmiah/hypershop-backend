"""Seller rating compute service — pulls metrics + applies weighted formula."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.seller_rating import repository as repo
from app.modules.seller_rating.codes import (
    LOOKBACK_DAYS,
    MIN_ORDERS_FOR_FULL_SCORE,
    NEUTRAL_COMPONENT_SCORE,
    TIER_GOLD,
    TIER_PLATINUM,
    TIER_POOR,
    TIER_SILVER,
    TIER_STANDARD,
    TIER_SUSPENDED,
    WEIGHT_DISPUTE,
    WEIGHT_ON_TIME,
    WEIGHT_RESPONSE,
    WEIGHT_RETURN,
    WEIGHT_REVIEW,
    WEIGHT_VOLUME,
)

_log = get_logger("hypershop.seller_rating.service")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def score_to_tier(score: float) -> str:
    if score >= 90:
        return TIER_PLATINUM
    if score >= 75:
        return TIER_GOLD
    if score >= 60:
        return TIER_SILVER
    if score >= 40:
        return TIER_STANDARD
    return TIER_POOR


def compute_overall_score(metrics: dict[str, Any]) -> float:
    on_time = metrics.get("on_time")
    return_rate = metrics.get("return")
    dispute_rate = metrics.get("dispute")
    response = metrics.get("response")
    review = metrics.get("review")
    orders = metrics.get("orders") or 0

    if on_time is None:
        on_time_score = NEUTRAL_COMPONENT_SCORE
    else:
        on_time_score = max(0.0, min(100.0, float(on_time) * 100.0))

    if return_rate is None:
        return_score = NEUTRAL_COMPONENT_SCORE
    else:
        return_score = max(0.0, min(100.0, (1.0 - float(return_rate)) * 100.0))

    if dispute_rate is None:
        dispute_score = NEUTRAL_COMPONENT_SCORE
    else:
        dispute_score = max(0.0, min(100.0, float(dispute_rate) * 100.0))

    if response is None:
        response_score = NEUTRAL_COMPONENT_SCORE
    else:
        hours = float(response)
        if hours <= 1.0:
            response_score = 100.0
        elif hours >= 48.0:
            response_score = 0.0
        else:
            response_score = max(0.0, 100.0 - ((hours - 1.0) / 47.0) * 100.0)

    if review is None:
        review_score = NEUTRAL_COMPONENT_SCORE
    else:
        review_score = max(0.0, min(100.0, (float(review) / 5.0) * 100.0))

    volume_score = min(100.0, (float(orders) / MIN_ORDERS_FOR_FULL_SCORE) * 100.0)

    overall = (
        WEIGHT_ON_TIME * on_time_score
        + WEIGHT_RETURN * return_score
        + WEIGHT_DISPUTE * dispute_score
        + WEIGHT_RESPONSE * response_score
        + WEIGHT_REVIEW * review_score
        + WEIGHT_VOLUME * volume_score
    )
    return round(max(0.0, min(100.0, overall)), 2)


async def _safe_scalar(
    session: AsyncSession, sql: str, params: dict[str, Any],
) -> Any:
    try:
        return (await session.execute(text(sql), params)).scalar()
    except Exception as exc:  # noqa: BLE001
        _log.warning("seller_rating_metric_query_failed err=%s", exc)
        return None


async def metrics_on_time_shipping(
    session: AsyncSession, seller_id: UUID, since: datetime,
) -> float | None:
    sql = """
        SELECT
          COUNT(*) FILTER (
            WHERE o.dispatched_at IS NOT NULL
              AND o.dispatched_at <= o.placed_at + INTERVAL '3 days'
          )::float
          / NULLIF(COUNT(*) FILTER (WHERE o.dispatched_at IS NOT NULL), 0)
        FROM orders o
        JOIN order_lines ol ON ol.order_id = o.id
        JOIN product_variants pv ON pv.id = ol.variant_id
        JOIN products p ON p.id = pv.product_id
        WHERE p.seller_id = :seller_id
          AND o.placed_at >= :since
    """
    val = await _safe_scalar(
        session, sql, {"seller_id": seller_id, "since": since},
    )
    return float(val) if val is not None else None


async def metrics_return_rate(
    session: AsyncSession, seller_id: UUID, since: datetime,
) -> float | None:
    sql = """
        WITH seller_orders AS (
          SELECT DISTINCT o.id AS order_id
          FROM orders o
          JOIN order_lines ol ON ol.order_id = o.id
          JOIN product_variants pv ON pv.id = ol.variant_id
          JOIN products p ON p.id = pv.product_id
          WHERE p.seller_id = :seller_id
            AND o.placed_at >= :since
        )
        SELECT
          (SELECT COUNT(*) FROM return_requests r
             WHERE r.order_id IN (SELECT order_id FROM seller_orders)
               AND r.requested_at >= :since)::float
          / NULLIF((SELECT COUNT(*) FROM seller_orders), 0)
    """
    val = await _safe_scalar(
        session, sql, {"seller_id": seller_id, "since": since},
    )
    return float(val) if val is not None else None


async def metrics_dispute_resolution(
    session: AsyncSession, seller_id: UUID, since: datetime,
) -> float | None:
    sql = """
        SELECT
          COUNT(*) FILTER (
            WHERE status IN ('resolved','closed')
              AND resolution IS NOT NULL
              AND resolution <> 'decline'
          )::float
          / NULLIF(COUNT(*), 0)
        FROM hypershop_disputes
        WHERE seller_id = :seller_id
          AND created_at >= :since
    """
    val = await _safe_scalar(
        session, sql, {"seller_id": seller_id, "since": since},
    )
    return float(val) if val is not None else None


async def metrics_response_time(
    session: AsyncSession, seller_id: UUID, since: datetime,
) -> float | None:
    sql = """
        SELECT AVG(
          EXTRACT(EPOCH FROM (last_response_at - opened_at)) / 3600.0
        )
        FROM hypershop_disputes
        WHERE seller_id = :seller_id
          AND last_response_at IS NOT NULL
          AND created_at >= :since
    """
    val = await _safe_scalar(
        session, sql, {"seller_id": seller_id, "since": since},
    )
    return float(val) if val is not None else None


async def metrics_review_average(
    session: AsyncSession, seller_id: UUID,
) -> tuple[float | None, int]:
    cutoff = _now() - timedelta(days=90)
    sql = """
        SELECT AVG(r.rating)::float, COUNT(*)
        FROM product_reviews r
        JOIN products p ON p.id = r.product_id
        WHERE p.seller_id = :seller_id
          AND r.status = 'approved'
          AND r.created_at >= :since
    """
    try:
        row = (
            await session.execute(
                text(sql), {"seller_id": seller_id, "since": cutoff},
            )
        ).first()
        if row is None:
            return None, 0
        avg, count = row
        return (float(avg) if avg is not None else None, int(count or 0))
    except Exception as exc:  # noqa: BLE001
        _log.warning("seller_rating_review_query_failed err=%s", exc)
        return None, 0


async def _metrics_order_count(
    session: AsyncSession, seller_id: UUID, since: datetime,
) -> int:
    sql = """
        SELECT COUNT(DISTINCT o.id)
        FROM orders o
        JOIN order_lines ol ON ol.order_id = o.id
        JOIN product_variants pv ON pv.id = ol.variant_id
        JOIN products p ON p.id = pv.product_id
        WHERE p.seller_id = :seller_id
          AND o.placed_at >= :since
    """
    val = await _safe_scalar(
        session, sql, {"seller_id": seller_id, "since": since},
    )
    return int(val or 0)


async def compute_rating_for_seller(
    session: AsyncSession, seller_id: UUID,
) -> dict[str, Any]:
    since = _now() - timedelta(days=LOOKBACK_DAYS)
    on_time = await metrics_on_time_shipping(session, seller_id, since)
    return_rate = await metrics_return_rate(session, seller_id, since)
    dispute_rate = await metrics_dispute_resolution(session, seller_id, since)
    response = await metrics_response_time(session, seller_id, since)
    review_avg, review_count = await metrics_review_average(session, seller_id)
    order_count = await _metrics_order_count(session, seller_id, since)

    metrics_payload = {
        "on_time": on_time,
        "return": return_rate,
        "dispute": dispute_rate,
        "response": response,
        "review": review_avg,
        "review_count": review_count,
        "orders": order_count,
    }
    score = compute_overall_score(metrics_payload)

    existing = await repo.get_rating(session, seller_id)
    if existing is not None and existing.tier == TIER_SUSPENDED:
        # Suspended sellers keep their admin-flagged tier; we still
        # snapshot the computed score for audit history.
        tier = TIER_SUSPENDED
    else:
        tier = score_to_tier(score)

    now = _now()
    await repo.upsert_rating(
        session,
        seller_id,
        overall_score=Decimal(str(score)),
        tier=tier,
        on_time_shipping_rate=(
            Decimal(str(round(on_time, 4))) if on_time is not None else None
        ),
        return_rate=(
            Decimal(str(round(return_rate, 4)))
            if return_rate is not None else None
        ),
        dispute_resolution_rate=(
            Decimal(str(round(dispute_rate, 4)))
            if dispute_rate is not None else None
        ),
        response_time_hours=(
            Decimal(str(round(response, 2)))
            if response is not None else None
        ),
        review_avg=(
            Decimal(str(round(review_avg, 2)))
            if review_avg is not None else None
        ),
        review_count=review_count,
        order_count_30d=order_count,
        last_computed_at=now,
    )
    await repo.insert_snapshot(
        session,
        seller_id,
        overall_score=Decimal(str(score)),
        tier=tier,
        metrics=metrics_payload,
    )
    _log.info(
        "seller_rating_computed seller_id=%s score=%s tier=%s",
        seller_id, score, tier,
    )
    return {
        "seller_id": str(seller_id),
        "overall_score": score,
        "tier": tier,
        "metrics": metrics_payload,
    }


async def suspend_seller(
    session: AsyncSession, seller_id: UUID, reason: str,
) -> dict[str, Any]:
    row = await repo.set_tier(session, seller_id, TIER_SUSPENDED)
    _log.info(
        "seller_rating_suspended seller_id=%s reason=%s", seller_id, reason,
    )
    return {
        "seller_id": str(row.seller_id),
        "tier": row.tier,
        "reason": reason,
    }


async def unsuspend_seller(
    session: AsyncSession, seller_id: UUID,
) -> dict[str, Any]:
    await repo.set_tier(session, seller_id, TIER_STANDARD)
    return await compute_rating_for_seller(session, seller_id)
