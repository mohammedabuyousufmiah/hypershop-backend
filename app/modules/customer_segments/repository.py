"""Async CRUD helpers for customer_segments."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customer_segments.models import (
    HypershopCustomerRfmScore,
    HypershopCustomerSegment,
    HypershopCustomerSegmentMembership,
)


async def upsert_rfm_score(
    session: AsyncSession, customer_user_id: UUID, **fields: Any,
) -> HypershopCustomerRfmScore:
    """Insert-or-update the RFM row keyed by customer_user_id."""
    payload = {"customer_user_id": customer_user_id, **fields}
    stmt = (
        pg_insert(HypershopCustomerRfmScore)
        .values(**payload)
        .on_conflict_do_update(
            index_elements=["customer_user_id"],
            set_={
                k: v for k, v in payload.items() if k != "customer_user_id"
            },
        )
        .returning(HypershopCustomerRfmScore)
    )
    r = await session.execute(stmt)
    await session.flush()
    return r.scalar_one()


async def get_rfm_score(
    session: AsyncSession, customer_user_id: UUID,
) -> HypershopCustomerRfmScore | None:
    return await session.get(HypershopCustomerRfmScore, customer_user_id)


async def list_customers_in_segment(
    session: AsyncSession,
    segment_code: str,
    *,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[HypershopCustomerRfmScore], int]:
    base = select(HypershopCustomerRfmScore).where(
        HypershopCustomerRfmScore.segment_code == segment_code,
    )
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                base.order_by(HypershopCustomerRfmScore.rfm_total.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def list_segments(
    session: AsyncSession,
    *,
    is_active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopCustomerSegment], int]:
    stmt = select(HypershopCustomerSegment)
    if is_active is not None:
        stmt = stmt.where(HypershopCustomerSegment.is_active == is_active)
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(HypershopCustomerSegment.code)
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def get_segment(
    session: AsyncSession, segment_id: UUID,
) -> HypershopCustomerSegment | None:
    return await session.get(HypershopCustomerSegment, segment_id)


async def get_segment_by_code(
    session: AsyncSession, code: str,
) -> HypershopCustomerSegment | None:
    stmt = select(HypershopCustomerSegment).where(
        HypershopCustomerSegment.code == code,
    )
    return (await session.execute(stmt)).scalars().first()


async def create_segment(
    session: AsyncSession, **fields: Any,
) -> HypershopCustomerSegment:
    row = HypershopCustomerSegment(**fields)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_segment(
    session: AsyncSession, segment_id: UUID, **fields: Any,
) -> HypershopCustomerSegment:
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        fields["updated_at"] = datetime.now(timezone.utc)
        await session.execute(
            update(HypershopCustomerSegment)
            .where(HypershopCustomerSegment.id == segment_id)
            .values(**fields)
        )
    row = await session.get(HypershopCustomerSegment, segment_id)
    if row is None:
        raise LookupError(f"Segment {segment_id} not found")
    return row


async def replace_memberships(
    session: AsyncSession,
    segment_id: UUID,
    customer_ids: list[UUID],
) -> int:
    """Delete existing memberships for segment then bulk-insert new set.
    Returns count of inserted rows."""
    await session.execute(
        delete(HypershopCustomerSegmentMembership)
        .where(HypershopCustomerSegmentMembership.segment_id == segment_id)
    )
    if not customer_ids:
        return 0
    seen: set[UUID] = set()
    rows: list[dict[str, Any]] = []
    for cid in customer_ids:
        if cid in seen:
            continue
        seen.add(cid)
        rows.append({"segment_id": segment_id, "customer_user_id": cid})
    if not rows:
        return 0
    await session.execute(
        pg_insert(HypershopCustomerSegmentMembership).values(rows)
    )
    return len(rows)


async def list_memberships(
    session: AsyncSession,
    segment_id: UUID,
    *,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[HypershopCustomerSegmentMembership], int]:
    base = select(HypershopCustomerSegmentMembership).where(
        HypershopCustomerSegmentMembership.segment_id == segment_id,
    )
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                base.order_by(
                    HypershopCustomerSegmentMembership.added_at.desc(),
                )
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def list_active_customer_ids(
    session: AsyncSession, lookback_days: int, *, limit: int = 1000,
    offset: int = 0,
) -> list[UUID]:
    """Customer ids with at least one order in the lookback window."""
    sql = text(
        """
        SELECT DISTINCT customer_user_id
          FROM orders
         WHERE placed_at >= (now() AT TIME ZONE 'UTC')
                            - make_interval(days => :days)
         ORDER BY customer_user_id
         LIMIT :lim OFFSET :off
        """
    )
    r = await session.execute(
        sql, {"days": lookback_days, "lim": limit, "off": offset},
    )
    return [row[0] for row in r.all()]


_ = update  # silence unused-import warnings
