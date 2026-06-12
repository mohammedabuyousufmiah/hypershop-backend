"""Async CRUD helpers for seller ratings."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.seller_rating.models import (
    HypershopSellerRating,
    HypershopSellerRatingSnapshot,
)


async def get_rating(
    session: AsyncSession, seller_id: UUID,
) -> HypershopSellerRating | None:
    return await session.get(HypershopSellerRating, seller_id)


async def upsert_rating(
    session: AsyncSession, seller_id: UUID, **fields: Any,
) -> HypershopSellerRating:
    fields["updated_at"] = datetime.now(timezone.utc)
    fields["last_computed_at"] = fields.get(
        "last_computed_at", datetime.now(timezone.utc),
    )
    stmt = pg_insert(HypershopSellerRating).values(
        seller_id=seller_id, **fields,
    )
    update_cols = {k: stmt.excluded[k] for k in fields.keys()}
    stmt = stmt.on_conflict_do_update(
        index_elements=["seller_id"],
        set_=update_cols,
    )
    await session.execute(stmt)
    return await session.get(HypershopSellerRating, seller_id)


async def insert_snapshot(
    session: AsyncSession, seller_id: UUID, **fields: Any,
) -> HypershopSellerRatingSnapshot:
    row = HypershopSellerRatingSnapshot(seller_id=seller_id, **fields)
    session.add(row)
    await session.flush()
    return row


async def list_ratings(
    session: AsyncSession,
    *,
    tier: str | None = None,
    min_score: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopSellerRating], int]:
    stmt = select(HypershopSellerRating)
    if tier:
        stmt = stmt.where(HypershopSellerRating.tier == tier)
    if min_score is not None:
        stmt = stmt.where(HypershopSellerRating.overall_score >= min_score)
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(HypershopSellerRating.overall_score.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def list_snapshots(
    session: AsyncSession, seller_id: UUID, *, limit: int = 90,
) -> list[HypershopSellerRatingSnapshot]:
    stmt = (
        select(HypershopSellerRatingSnapshot)
        .where(HypershopSellerRatingSnapshot.seller_id == seller_id)
        .order_by(HypershopSellerRatingSnapshot.computed_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_all_active_seller_ids(
    session: AsyncSession, *, limit: int = 500,
) -> list[UUID]:
    stmt = text(
        "SELECT id FROM sellers WHERE status = 'approved' "
        "ORDER BY id LIMIT :lim"
    )
    rows = (await session.execute(stmt, {"lim": limit})).all()
    return [r[0] for r in rows]


async def set_tier(
    session: AsyncSession, seller_id: UUID, tier: str,
) -> HypershopSellerRating | None:
    existing = await session.get(HypershopSellerRating, seller_id)
    now = datetime.now(timezone.utc)
    if existing is None:
        row = HypershopSellerRating(
            seller_id=seller_id,
            overall_score=50,
            tier=tier,
            last_computed_at=now,
        )
        session.add(row)
        await session.flush()
        return row
    await session.execute(
        update(HypershopSellerRating)
        .where(HypershopSellerRating.seller_id == seller_id)
        .values(tier=tier, updated_at=now)
    )
    return await session.get(HypershopSellerRating, seller_id)
