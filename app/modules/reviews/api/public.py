"""Public review surface — list + aggregate. Anonymous-safe.

Two endpoints:

  * ``GET /products/{product_id}/reviews`` — paginated list of
    APPROVED reviews; default sort = helpful then newest.
  * ``GET /products/{product_id}/rating`` — cached aggregate
    (avg_rating + review_count). Returns zeros when no reviews yet.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.reviews.repository import ReviewRepository
from app.modules.reviews.schemas import (
    ProductRatingOut,
    PublicReviewOut,
    PublicReviewWithMediaOut,
    ReviewListOut,
    ReviewListWithMediaOut,
    ReviewMediaOut,
)
from app.modules.reviews.service import ReviewService

router = APIRouter(tags=["reviews-public"])


async def _serialize_public(
    session: AsyncSession,
    items: list,
) -> list[PublicReviewOut]:
    """Hydrate display_name from the user row without exposing PII."""
    if not items:
        return []
    from app.modules.iam.models import User
    from sqlalchemy import select

    user_ids = list({r.customer_id for r in items})
    name_rows = (
        await session.execute(
            select(User.id, User.full_name).where(User.id.in_(user_ids)),
        )
    ).all()
    name_map: dict[UUID, str | None] = {row[0]: row[1] for row in name_rows}

    out: list[PublicReviewOut] = []
    for r in items:
        view = PublicReviewOut.model_validate(r)
        # Show the first name only (or "Customer" as a fallback) —
        # last names + emails stay private even on public reviews.
        full = name_map.get(r.customer_id) or ""
        view.customer_display_name = full.split()[0] if full else "Customer"
        out.append(view)
    return out


@router.get(
    "/products/{product_id}/reviews",
    response_model=ReviewListWithMediaOut,
)
async def list_reviews(
    product_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    sort: Annotated[Literal["helpful", "newest"], Query()] = "helpful",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ReviewListWithMediaOut:
    """Phase-2 response shape includes attached photos for each
    approved review. Pre-phase-2 clients that ignore the new ``media``
    field continue to work — it's an additive change."""
    async with uow.transactional() as session:
        svc = ReviewService(session)
        items, total = await svc.list_public(
            product_id, sort=sort, offset=offset, limit=limit,
        )
        rows = list(items)
        public_views = await _serialize_public(session, rows)

        # Bulk-fetch media for all returned review ids (single query
        # rather than N+1) and group client-side.
        media_rows = await svc.list_media_for_reviews(
            [r.id for r in rows],
        )
        by_review: dict = {}
        for m in media_rows:
            by_review.setdefault(m.review_id, []).append(
                ReviewMediaOut.model_validate(m),
            )

        with_media: list[PublicReviewWithMediaOut] = []
        for view in public_views:
            entry = PublicReviewWithMediaOut(
                **view.model_dump(),
                media=by_review.get(view.id, []),
            )
            with_media.append(entry)
    return ReviewListWithMediaOut(items=with_media, total=total)


@router.get(
    "/products/{product_id}/rating",
    response_model=ProductRatingOut,
)
async def get_rating(
    product_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ProductRatingOut:
    from decimal import Decimal

    async with uow.transactional() as session:
        repo = ReviewRepository(session)
        agg = await repo.get_aggregate(product_id)
    if agg is None:
        return ProductRatingOut(
            product_id=product_id,
            avg_rating=Decimal("0.00"),
            review_count=0,
        )
    return ProductRatingOut(
        product_id=product_id,
        avg_rating=agg.avg_rating,
        review_count=agg.review_count,
    )
