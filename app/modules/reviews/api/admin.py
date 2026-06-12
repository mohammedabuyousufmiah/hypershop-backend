"""Admin moderation endpoints — `reviews.admin` permission required.

  * GET   /admin/reviews?status=pending&offset=&limit=
  * POST  /admin/reviews/{review_id}/approve
  * POST  /admin/reviews/{review_id}/reject     (body: reason)
  * POST  /admin/reviews/{review_id}/disable
  * POST  /admin/reviews/{review_id}/reenable
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ValidationError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.reviews.codes import ALL_STATUSES
from app.modules.reviews.schemas import (
    AdminReviewListOut,
    AdminReviewOut,
    ReviewRejectIn,
)
from app.modules.reviews.service import ReviewService

router = APIRouter(prefix="/admin/reviews", tags=["admin-reviews"])

_RW = "reviews.admin"


@router.get(
    "",
    response_model=AdminReviewListOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_reviews_admin(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[
        str | None,
        Query(description="Filter by status; omit for all."),
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminReviewListOut:
    if status and status not in ALL_STATUSES:
        raise ValidationError(
            f"Unknown status: {status}.",
            details={"allowed": list(ALL_STATUSES)},
        )
    async with uow.transactional() as session:
        svc = ReviewService(session)
        items, total = await svc.list_admin(
            status=status, offset=offset, limit=limit,
        )
        rows = [AdminReviewOut.model_validate(r) for r in items]
    return AdminReviewListOut(items=rows, total=total)


@router.post(
    "/{review_id}/approve",
    response_model=AdminReviewOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def approve_review(
    review_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminReviewOut:
    async with uow.transactional() as session:
        svc = ReviewService(session)
        review = await svc.approve(review_id=review_id, principal=principal)
        await session.refresh(review)
        return AdminReviewOut.model_validate(review)


@router.post(
    "/{review_id}/reject",
    response_model=AdminReviewOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def reject_review(
    review_id: Annotated[UUID, PathParam(...)],
    body: ReviewRejectIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminReviewOut:
    async with uow.transactional() as session:
        svc = ReviewService(session)
        review = await svc.reject(
            review_id=review_id, reason=body.reason, principal=principal,
        )
        await session.refresh(review)
        return AdminReviewOut.model_validate(review)


@router.post(
    "/{review_id}/disable",
    response_model=AdminReviewOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def disable_review(
    review_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminReviewOut:
    async with uow.transactional() as session:
        svc = ReviewService(session)
        review = await svc.disable(review_id=review_id, principal=principal)
        await session.refresh(review)
        return AdminReviewOut.model_validate(review)


@router.post(
    "/{review_id}/reenable",
    response_model=AdminReviewOut,
    dependencies=[Depends(requires_permission(_RW))],
)
async def reenable_review(
    review_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminReviewOut:
    async with uow.transactional() as session:
        svc = ReviewService(session)
        review = await svc.reenable(review_id=review_id, principal=principal)
        await session.refresh(review)
        return AdminReviewOut.model_validate(review)
