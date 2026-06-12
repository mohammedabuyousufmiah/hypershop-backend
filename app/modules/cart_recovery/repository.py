"""Async CRUD helpers for cart_recovery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cart_recovery.codes import (
    CART_MILESTONES,
    MILESTONE_MINUTES,
    WINBACK_MILESTONES,
)
from app.modules.cart_recovery.models import (
    HypershopCartRecoveryDispatch,
    HypershopCartRecoverySuppression,
)


async def get_dispatch_by_cart_and_milestone(
    session: AsyncSession, cart_id: UUID, milestone: str,
) -> HypershopCartRecoveryDispatch | None:
    """Lookup the unique row per (cart, milestone)."""
    stmt = select(HypershopCartRecoveryDispatch).where(
        HypershopCartRecoveryDispatch.cart_id == cart_id,
        HypershopCartRecoveryDispatch.milestone == milestone,
    )
    return (await session.execute(stmt)).scalars().first()


async def create_dispatch(
    session: AsyncSession, **fields: Any,
) -> HypershopCartRecoveryDispatch:
    """Insert a dispatch row; caller commits."""
    row = HypershopCartRecoveryDispatch(**fields)
    session.add(row)
    await session.flush()
    return row


async def update_dispatch(
    session: AsyncSession, dispatch_id: int, **fields: Any,
) -> None:
    """Patch a dispatch row by id."""
    await session.execute(
        update(HypershopCartRecoveryDispatch)
        .where(HypershopCartRecoveryDispatch.id == dispatch_id)
        .values(**fields)
    )


async def count_recent_sends(
    session: AsyncSession, customer_user_id: UUID, since: datetime,
) -> int:
    """Count actual (sent or log_only) dispatches in window for cap check."""
    stmt = select(
        text("COUNT(*)")
    ).select_from(HypershopCartRecoveryDispatch).where(
        HypershopCartRecoveryDispatch.customer_user_id == customer_user_id,
        HypershopCartRecoveryDispatch.created_at >= since,
        HypershopCartRecoveryDispatch.status.in_(("sent", "log_only")),
    )
    r = await session.execute(stmt)
    return int(r.scalar() or 0)


async def is_suppressed(
    session: AsyncSession,
    *,
    customer_user_id: UUID | None,
    channel: str,
    email: str | None = None,
    phone: str | None = None,
) -> tuple[bool, str | None]:
    """Returns (True, reason) when an active suppression matches."""
    now = datetime.now(timezone.utc)
    conds = []
    if customer_user_id is not None:
        conds.append(
            HypershopCartRecoverySuppression.customer_user_id == customer_user_id,
        )
    if email:
        conds.append(HypershopCartRecoverySuppression.key_email == email)
    if phone:
        conds.append(HypershopCartRecoverySuppression.key_phone == phone)
    if not conds:
        return (False, None)

    stmt = select(
        HypershopCartRecoverySuppression.reason,
    ).where(
        or_(*conds),
        HypershopCartRecoverySuppression.channel.in_((channel, "all")),
        or_(
            HypershopCartRecoverySuppression.expires_at.is_(None),
            HypershopCartRecoverySuppression.expires_at > now,
        ),
    ).limit(1)
    r = (await session.execute(stmt)).first()
    if r is None:
        return (False, None)
    return (True, r[0])


async def list_carts_due_for_milestone(
    session: AsyncSession, milestone: str, *, limit: int = 500,
) -> list[dict[str, Any]]:
    """Open carts past the milestone idle window with no dispatch yet."""
    if milestone not in CART_MILESTONES:
        return []
    minutes = MILESTONE_MINUTES[milestone]
    # Raw SQL — cleaner than join-and-correlate for this read.
    sql = text(
        """
        SELECT
            c.id            AS cart_id,
            c.user_id       AS customer_user_id,
            u.email         AS email,
            u.phone         AS phone,
            u.full_name     AS customer_name,
            COALESCE(ic.item_count, 0)        AS item_count,
            COALESCE(ic.cart_total_minor, 0)  AS cart_total_minor,
            c.updated_at    AS updated_at
        FROM carts c
        LEFT JOIN users u ON u.id = c.user_id
        LEFT JOIN (
            SELECT cart_id,
                   COUNT(*)::int                                       AS item_count,
                   COALESCE(SUM((price_snapshot * quantity * 100)::bigint), 0)
                                                                       AS cart_total_minor
              FROM cart_items
             GROUP BY cart_id
        ) ic ON ic.cart_id = c.id
        WHERE c.status = 'open'
          AND c.updated_at < (now() AT TIME ZONE 'UTC') - make_interval(mins => :mins)
          AND NOT EXISTS (
                SELECT 1
                  FROM hypershop_cart_recovery_dispatches d
                 WHERE d.cart_id = c.id AND d.milestone = :ms
          )
          AND COALESCE(ic.item_count, 0) > 0
        ORDER BY c.updated_at ASC
        LIMIT :lim
        """
    )
    rows = (await session.execute(
        sql, {"mins": minutes, "ms": milestone, "lim": limit}
    )).mappings().all()
    return [dict(r) for r in rows]


async def list_customers_due_for_winback(
    session: AsyncSession, milestone: str, *, limit: int = 500,
) -> list[dict[str, Any]]:
    """Customers whose last order is older than the window, no prior dispatch."""
    if milestone not in WINBACK_MILESTONES:
        return []
    minutes = MILESTONE_MINUTES[milestone]
    sql = text(
        """
        WITH last_order AS (
            SELECT customer_user_id, MAX(placed_at) AS last_at
              FROM orders
             GROUP BY customer_user_id
        )
        SELECT
            u.id          AS customer_user_id,
            u.email       AS email,
            u.phone       AS phone,
            u.full_name   AS customer_name,
            lo.last_at    AS last_order_at
        FROM last_order lo
        JOIN users u ON u.id = lo.customer_user_id
        WHERE lo.last_at < (now() AT TIME ZONE 'UTC') - make_interval(mins => :mins)
          AND NOT EXISTS (
                SELECT 1
                  FROM hypershop_cart_recovery_dispatches d
                 WHERE d.customer_user_id = u.id
                   AND d.milestone = :ms
                   AND d.created_at >= (now() AT TIME ZONE 'UTC') - INTERVAL '90 days'
          )
        ORDER BY lo.last_at ASC
        LIMIT :lim
        """
    )
    rows = (await session.execute(
        sql, {"mins": minutes, "ms": milestone, "lim": limit}
    )).mappings().all()
    return [dict(r) for r in rows]


async def has_recent_order(
    session: AsyncSession, customer_user_id: UUID, hours: int,
) -> bool:
    """True if customer placed an order in last N hours."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    sql = text(
        "SELECT 1 FROM orders WHERE customer_user_id = :uid "
        "AND placed_at >= :since LIMIT 1"
    )
    r = await session.execute(sql, {"uid": customer_user_id, "since": since})
    return r.first() is not None


async def record_recovery(
    session: AsyncSession,
    *,
    customer_user_id: UUID,
    order_id: UUID,
    ordered_at: datetime,
) -> int:
    """Stamp recovered_at + recovered_order_id on customer's unrecovered
    dispatches in the last 7 days. Returns row count touched."""
    window_start = ordered_at - timedelta(days=7)
    stmt = (
        update(HypershopCartRecoveryDispatch)
        .where(
            HypershopCartRecoveryDispatch.customer_user_id == customer_user_id,
            HypershopCartRecoveryDispatch.recovered_at.is_(None),
            HypershopCartRecoveryDispatch.created_at >= window_start,
            HypershopCartRecoveryDispatch.status.in_(("sent", "log_only")),
        )
        .values(recovered_at=ordered_at, recovered_order_id=order_id)
    )
    r = await session.execute(stmt)
    return int(r.rowcount or 0)


# Suppress unused-import warning when this module is imported standalone.
_ = and_
