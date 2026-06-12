"""Service layer for the reviews module — phase 1.

Owns:
- verified-purchase eligibility check
- one-review-per-(customer, product) enforcement
- 24h edit window
- moderation state transitions (pending → approved / rejected;
  approved ↔ disabled)
- aggregate recompute on every status change touching APPROVED
- helpful-vote (idempotent, no self-vote)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.logging import get_logger
from app.core.security.principal import Principal
from app.modules.reviews.codes import (
    ACTION_REVIEW_APPROVED,
    ACTION_REVIEW_CREATED,
    ACTION_REVIEW_DISABLED,
    ACTION_REVIEW_HELPFUL,
    ACTION_REVIEW_MEDIA_UPLOADED,
    ACTION_REVIEW_REENABLED,
    ACTION_REVIEW_REJECTED,
    EDIT_WINDOW_HOURS,
    MAX_MEDIA_PER_REVIEW,
    MEDIA_KIND_IMAGE,
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from app.modules.reviews.errors import (
    ReviewAlreadyExistsError,
    ReviewBadStateError,
    ReviewEditWindowExpiredError,
    ReviewHelpfulSelfVoteError,
    ReviewMediaTooManyError,
    ReviewNotFoundError,
    ReviewNotVerifiedPurchaserError,
)
from app.modules.reviews.models import ProductReview, ReviewMedia
from app.modules.reviews.repository import ReviewRepository

_logger = get_logger("hypershop.reviews.service")


class ReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ReviewRepository(session)

    # ---- customer-facing ----

    async def create(
        self,
        *,
        product_id: UUID,
        customer_id: UUID,
        rating: int,
        title: str | None,
        body: str,
        principal: Principal,
    ) -> ProductReview:
        order_id = await self.repo.verified_purchase_order_id(
            customer_id=customer_id, product_id=product_id,
        )
        if order_id is None:
            raise ReviewNotVerifiedPurchaserError()

        existing = await self.repo.get_by_customer_product(
            customer_id=customer_id, product_id=product_id,
        )
        if existing is not None:
            raise ReviewAlreadyExistsError(
                details={"existing_review_id": str(existing.id)},
            )

        # Phase-1 moderation policy: every new review starts ``pending``
        # until an admin approves. Phase 4 will hook AI moderation here
        # to auto-promote low-risk reviews to ``approved``.
        review = await self.repo.create(
            product_id=product_id,
            customer_id=customer_id,
            order_id=order_id,
            rating=rating,
            title=title,
            body=body,
            status=STATUS_PENDING,
        )
        await record_audit(
            actor=principal,
            action=ACTION_REVIEW_CREATED,
            resource_type="product_review",
            resource_id=review.id,
            metadata={
                "product_id": str(product_id),
                "rating": rating,
                "verified_purchase_order_id": str(order_id),
            },
        )
        return review

    async def edit(
        self,
        *,
        review_id: UUID,
        customer_id: UUID,
        rating: int | None,
        title: str | None,
        body: str | None,
    ) -> ProductReview:
        r = await self.repo.get(review_id)
        if r is None:
            raise ReviewNotFoundError()
        if r.customer_id != customer_id:
            # Don't leak existence — same code as not-found.
            raise ReviewNotFoundError()
        # Edit window is enforced AFTER ownership so an attacker can't
        # use the error to confirm review existence.
        cutoff = datetime.now(timezone.utc) - timedelta(hours=EDIT_WINDOW_HOURS)
        if r.created_at < cutoff:
            raise ReviewEditWindowExpiredError()
        # Approved → editing flips it back to pending so a re-moderation
        # pass catches edits that game the AI moderator (phase 4).
        was_approved = r.status == STATUS_APPROVED
        await self.repo.edit_body(
            review_id, rating=rating, title=title, body=body,
        )
        if was_approved:
            await self.repo.update_status(review_id, status=STATUS_PENDING)
            # Bumping a review off the approved list changes the aggregate.
            await self.repo.recompute_aggregate(r.product_id)
        refreshed = await self.repo.get(review_id)
        assert refreshed is not None
        return refreshed

    async def vote_helpful(
        self,
        *,
        review_id: UUID,
        customer_id: UUID,
        principal: Principal,
    ) -> tuple[int, bool]:
        """Returns (new_helpful_count, voted_now)."""
        r = await self.repo.get(review_id)
        if r is None or r.status != STATUS_APPROVED:
            # Don't surface non-approved reviews to public callers.
            raise ReviewNotFoundError()
        if r.customer_id == customer_id:
            raise ReviewHelpfulSelfVoteError()
        added = await self.repo.add_helpful_vote(
            review_id=review_id, customer_id=customer_id,
        )
        if not added:
            return r.helpful_count, False
        new_count = await self.repo.increment_helpful_count(review_id)
        await record_audit(
            actor=principal,
            action=ACTION_REVIEW_HELPFUL,
            resource_type="product_review",
            resource_id=review_id,
        )
        return new_count, True

    # ---- admin-facing ----

    async def approve(
        self, *, review_id: UUID, principal: Principal,
    ) -> ProductReview:
        r = await self._require(review_id)
        if r.status not in (STATUS_PENDING, STATUS_DISABLED):
            raise ReviewBadStateError(
                f"Review must be pending or disabled to approve "
                f"(current: {r.status}).",
                details={"current_status": r.status},
            )
        await self.repo.update_status(
            review_id,
            status=STATUS_APPROVED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await self.repo.recompute_aggregate(r.product_id)
        await record_audit(
            actor=principal,
            action=ACTION_REVIEW_APPROVED,
            resource_type="product_review",
            resource_id=review_id,
            metadata={"product_id": str(r.product_id)},
        )
        refreshed = await self.repo.get(review_id)
        assert refreshed is not None
        return refreshed

    async def reject(
        self, *, review_id: UUID, reason: str, principal: Principal,
    ) -> ProductReview:
        r = await self._require(review_id)
        if r.status != STATUS_PENDING:
            raise ReviewBadStateError(
                f"Review must be pending to reject "
                f"(current: {r.status}). Use /disable for approved.",
                details={"current_status": r.status},
            )
        await self.repo.update_status(
            review_id,
            status=STATUS_REJECTED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
            rejection_reason=reason,
        )
        await record_audit(
            actor=principal,
            action=ACTION_REVIEW_REJECTED,
            resource_type="product_review",
            resource_id=review_id,
            metadata={
                "product_id": str(r.product_id),
                "reason": reason,
            },
        )
        refreshed = await self.repo.get(review_id)
        assert refreshed is not None
        return refreshed

    async def disable(
        self, *, review_id: UUID, principal: Principal,
    ) -> ProductReview:
        r = await self._require(review_id)
        if r.status != STATUS_APPROVED:
            raise ReviewBadStateError(
                f"Only approved reviews can be disabled "
                f"(current: {r.status}).",
                details={"current_status": r.status},
            )
        await self.repo.update_status(
            review_id,
            status=STATUS_DISABLED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await self.repo.recompute_aggregate(r.product_id)
        await record_audit(
            actor=principal,
            action=ACTION_REVIEW_DISABLED,
            resource_type="product_review",
            resource_id=review_id,
            metadata={"product_id": str(r.product_id)},
        )
        refreshed = await self.repo.get(review_id)
        assert refreshed is not None
        return refreshed

    async def reenable(
        self, *, review_id: UUID, principal: Principal,
    ) -> ProductReview:
        r = await self._require(review_id)
        if r.status != STATUS_DISABLED:
            raise ReviewBadStateError(
                f"Only disabled reviews can be re-enabled "
                f"(current: {r.status}).",
                details={"current_status": r.status},
            )
        await self.repo.update_status(
            review_id,
            status=STATUS_APPROVED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await self.repo.recompute_aggregate(r.product_id)
        await record_audit(
            actor=principal,
            action=ACTION_REVIEW_REENABLED,
            resource_type="product_review",
            resource_id=review_id,
            metadata={"product_id": str(r.product_id)},
        )
        refreshed = await self.repo.get(review_id)
        assert refreshed is not None
        return refreshed

    # ---- read paths ----

    async def list_public(
        self,
        product_id: UUID,
        *,
        sort: str = "helpful",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[Sequence[ProductReview], int]:
        return await self.repo.list_public(
            product_id, sort=sort, offset=offset, limit=limit,
        )

    async def list_admin(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[ProductReview], int]:
        return await self.repo.list_admin(
            status=status, offset=offset, limit=limit,
        )

    # ---- phase 2: review media ----

    async def attach_photo(
        self,
        *,
        review_id: UUID,
        customer_id: UUID,
        url: str,
        object_key: str,
        content_type: str,
        file_size_bytes: int,
        principal: Principal,
    ) -> ReviewMedia:
        r = await self.repo.get(review_id)
        if r is None or r.customer_id != customer_id:
            # Don't leak existence to non-owners.
            raise ReviewNotFoundError()
        # Photos are accepted while a review is pending or approved.
        # Rejected / disabled reviews are off the customer-facing surface
        # and adding photos to them serves no purpose.
        if r.status not in (STATUS_PENDING, STATUS_APPROVED):
            raise ReviewBadStateError(
                f"Cannot attach photos to a review in '{r.status}' state.",
                details={"current_status": r.status},
            )
        # Cap on photos per review — keeps storage cost predictable
        # and the customer-facing PDP rendering manageable.
        existing_count = await self.repo.count_media_for_review(review_id)
        if existing_count >= MAX_MEDIA_PER_REVIEW:
            raise ReviewMediaTooManyError(
                details={
                    "current": existing_count,
                    "max": MAX_MEDIA_PER_REVIEW,
                },
            )
        media = await self.repo.add_media(
            review_id=review_id,
            kind=MEDIA_KIND_IMAGE,
            url=url,
            object_key=object_key,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            position=existing_count,
        )
        await record_audit(
            actor=principal,
            action=ACTION_REVIEW_MEDIA_UPLOADED,
            resource_type="product_review",
            resource_id=review_id,
            metadata={
                "media_id": str(media.id),
                "size": file_size_bytes,
                "content_type": content_type,
            },
        )
        return media

    async def list_media_for_review(
        self, review_id: UUID,
    ) -> list[ReviewMedia]:
        return await self.repo.list_media_for_review(review_id)

    async def list_media_for_reviews(
        self, review_ids: Sequence[UUID],
    ) -> list[ReviewMedia]:
        return await self.repo.list_media_for_reviews(review_ids)

    # ---- internals ----

    async def _require(self, review_id: UUID) -> ProductReview:
        r = await self.repo.get(review_id)
        if r is None:
            raise ReviewNotFoundError()
        return r
