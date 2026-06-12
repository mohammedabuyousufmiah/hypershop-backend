"""Async SQLAlchemy repository for the reviews tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.orders.models import Order, OrderLine
from app.modules.catalog.models import ProductVariant
from app.modules.reviews.codes import (
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    VERIFIED_PURCHASE_WINDOW_DAYS,
)
from app.modules.reviews.models import (
    ProductReview,
    ProductReviewAggregate,
    ReviewHelpfulVote,
    ReviewMedia,
)


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- create / read ----

    async def create(self, **fields: object) -> ProductReview:
        r = ProductReview(**fields)
        self.session.add(r)
        await self.session.flush()
        return r

    async def get(self, review_id: UUID) -> ProductReview | None:
        return await self.session.get(ProductReview, review_id)

    async def get_by_customer_product(
        self, *, customer_id: UUID, product_id: UUID,
    ) -> ProductReview | None:
        stmt = select(ProductReview).where(
            ProductReview.customer_id == customer_id,
            ProductReview.product_id == product_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_public(
        self,
        product_id: UUID,
        *,
        sort: str = "helpful",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[Sequence[ProductReview], int]:
        base = select(ProductReview).where(
            ProductReview.product_id == product_id,
            ProductReview.status == STATUS_APPROVED,
        )
        # Tiebreaker on created_at DESC matches the index
        # ``ix_reviews_product_helpful``.
        if sort == "newest":
            base = base.order_by(ProductReview.created_at.desc())
        else:  # "helpful" (default)
            base = base.order_by(
                ProductReview.helpful_count.desc(),
                ProductReview.created_at.desc(),
            )
        items_stmt = base.offset(offset).limit(limit)
        items = (await self.session.execute(items_stmt)).scalars().all()
        total_stmt = (
            select(func.count())
            .select_from(ProductReview)
            .where(
                ProductReview.product_id == product_id,
                ProductReview.status == STATUS_APPROVED,
            )
        )
        total = int((await self.session.execute(total_stmt)).scalar_one())
        return items, total

    async def list_admin(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[ProductReview], int]:
        base = select(ProductReview)
        if status:
            base = base.where(ProductReview.status == status)
        items = (
            await self.session.execute(
                base.order_by(ProductReview.created_at.desc())
                .offset(offset).limit(limit),
            )
        ).scalars().all()
        total_stmt = select(func.count()).select_from(ProductReview)
        if status:
            total_stmt = total_stmt.where(ProductReview.status == status)
        total = int((await self.session.execute(total_stmt)).scalar_one())
        return items, total

    # ---- updates ----

    async def update_status(
        self,
        review_id: UUID,
        *,
        status: str,
        moderated_by: UUID | None = None,
        moderated_at: datetime | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        values: dict[str, object] = {"status": status}
        if moderated_by is not None:
            values["moderated_by"] = moderated_by
        if moderated_at is not None:
            values["moderated_at"] = moderated_at
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason
        await self.session.execute(
            update(ProductReview)
            .where(ProductReview.id == review_id)
            .values(**values),
        )

    async def edit_body(
        self,
        review_id: UUID,
        *,
        rating: int | None = None,
        title: str | None = None,
        body: str | None = None,
    ) -> None:
        values: dict[str, object] = {}
        if rating is not None:
            values["rating"] = rating
        if title is not None:
            values["title"] = title
        if body is not None:
            values["body"] = body
        if not values:
            return
        await self.session.execute(
            update(ProductReview)
            .where(ProductReview.id == review_id)
            .values(**values),
        )

    # ---- verified-purchase check ----

    async def verified_purchase_order_id(
        self, *, customer_id: UUID, product_id: UUID,
    ) -> UUID | None:
        """Return the most recent eligible order_id, or None."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=VERIFIED_PURCHASE_WINDOW_DAYS,
        )
        stmt = (
            select(Order.id)
            .join(OrderLine, OrderLine.order_id == Order.id)
            .join(ProductVariant, ProductVariant.id == OrderLine.variant_id)
            .where(
                Order.customer_user_id == customer_id,
                ProductVariant.product_id == product_id,
                Order.status == "completed",
                Order.completed_at >= cutoff,
            )
            .order_by(Order.completed_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---- helpful votes ----

    async def add_helpful_vote(
        self, *, review_id: UUID, customer_id: UUID,
    ) -> bool:
        """Insert vote idempotently. Returns True if a new row landed."""
        # ON CONFLICT keeps the call idempotent without a SELECT pre-check.
        # We still need to know whether a row was actually inserted so the
        # service knows whether to bump helpful_count.
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(ReviewHelpfulVote)
            .values(review_id=review_id, customer_id=customer_id)
            .on_conflict_do_nothing(
                index_elements=["review_id", "customer_id"],
            )
            .returning(ReviewHelpfulVote.review_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def increment_helpful_count(self, review_id: UUID) -> int:
        stmt = (
            update(ProductReview)
            .where(ProductReview.id == review_id)
            .values(helpful_count=ProductReview.helpful_count + 1)
            .returning(ProductReview.helpful_count)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    # ---- aggregate ----

    async def recompute_aggregate(self, product_id: UUID) -> tuple[Decimal, int]:
        """Recompute avg_rating + review_count from APPROVED rows only."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        # Exclude pending/rejected/disabled — only public-visible
        # reviews count toward the customer-facing average.
        agg_stmt = (
            select(
                func.coalesce(
                    func.round(func.avg(ProductReview.rating), 2),
                    0,
                ),
                func.count(),
            )
            .where(
                ProductReview.product_id == product_id,
                ProductReview.status == STATUS_APPROVED,
            )
        )
        avg_raw, count = (await self.session.execute(agg_stmt)).one()
        avg = Decimal(str(avg_raw))

        upsert = (
            pg_insert(ProductReviewAggregate)
            .values(
                product_id=product_id,
                avg_rating=avg,
                review_count=int(count),
            )
            .on_conflict_do_update(
                index_elements=["product_id"],
                set_={
                    "avg_rating": avg,
                    "review_count": int(count),
                    "updated_at": func.now(),
                },
            )
        )
        await self.session.execute(upsert)
        return avg, int(count)

    async def get_aggregate(
        self, product_id: UUID,
    ) -> ProductReviewAggregate | None:
        return await self.session.get(ProductReviewAggregate, product_id)

    # ---- review media (phase 2) ----

    async def add_media(self, **fields: object) -> ReviewMedia:
        m = ReviewMedia(**fields)
        self.session.add(m)
        await self.session.flush()
        return m

    async def count_media_for_review(self, review_id: UUID) -> int:
        stmt = select(func.count()).select_from(ReviewMedia).where(
            ReviewMedia.review_id == review_id,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_media_for_reviews(
        self, review_ids: Sequence[UUID],
    ) -> list[ReviewMedia]:
        if not review_ids:
            return []
        stmt = (
            select(ReviewMedia)
            .where(ReviewMedia.review_id.in_(review_ids))
            .order_by(ReviewMedia.review_id, ReviewMedia.position, ReviewMedia.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_media_for_review(
        self, review_id: UUID,
    ) -> list[ReviewMedia]:
        return await self.list_media_for_reviews([review_id])
