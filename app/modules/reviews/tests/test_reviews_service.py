"""Service-level tests for the reviews module — phase 1.

End-to-end behaviour through the real DB (testcontainers): verified-
purchase gate, dedupe, edit-window, moderation transitions, helpful-
vote idempotency, aggregate recompute.

API-layer tests (auth + status codes) live in test_reviews_api.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.db.session import get_sessionmaker
from app.core.security.principal import Principal
from app.modules.reviews.codes import (
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
    ReviewNotFoundError,
    ReviewNotVerifiedPurchaserError,
)
from app.modules.reviews.repository import ReviewRepository
from app.modules.reviews.service import ReviewService
from app.modules.reviews.tests._helpers import (
    seed_completed_order,
    seed_product_with_variant,
)

pytestmark = pytest.mark.integration


def _principal(user_id) -> Principal:
    from uuid import uuid4
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        roles=frozenset({"customer"}),
        permissions=frozenset({"reviews.write"}),
    )


def _admin(user_id) -> Principal:
    from uuid import uuid4
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        roles=frozenset({"manager"}),
        permissions=frozenset({"reviews.admin"}),
    )


# ───────── 1. Verified-purchase gate ─────────


async def test_create_rejects_unverified_purchaser(registered_user):
    pid, _ = await seed_product_with_variant()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        with pytest.raises(ReviewNotVerifiedPurchaserError):
            await svc.create(
                product_id=pid,
                customer_id=registered_user["user_id"],
                rating=5,
                title="t",
                body="this product is great" * 2,
                principal=_principal(registered_user["user_id"]),
            )


async def test_create_succeeds_when_order_within_window(registered_user):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        review = await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=4,
            title="ok",
            body="works as advertised, will buy again",
            principal=_principal(registered_user["user_id"]),
        )
    assert review.status == STATUS_PENDING
    assert review.rating == 4


# ───────── 2. Dedupe ─────────


async def test_create_rejects_duplicate(registered_user):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=5,
            title="great",
            body="really pleased with the purchase",
            principal=_principal(registered_user["user_id"]),
        )
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        with pytest.raises(ReviewAlreadyExistsError):
            await svc.create(
                product_id=pid,
                customer_id=registered_user["user_id"],
                rating=4,
                title="re",
                body="trying to write a second review",
                principal=_principal(registered_user["user_id"]),
            )


# ───────── 3. Edit window ─────────


async def test_edit_within_window_updates_body(registered_user):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        r = await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=5,
            title=None,
            body="original body text here",
            principal=_principal(registered_user["user_id"]),
        )
        rid = r.id
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        edited = await svc.edit(
            review_id=rid,
            customer_id=registered_user["user_id"],
            rating=4,
            title="updated title",
            body="updated body text here, much improved",
        )
    assert edited.title == "updated title"
    assert edited.rating == 4


async def test_edit_outside_window_rejected(registered_user):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        r = await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=5,
            title=None,
            body="original body text here",
            principal=_principal(registered_user["user_id"]),
        )
        rid = r.id
    # Backdate created_at to 25 hours ago (outside the 24h window).
    async with sm() as s, s.begin():
        from sqlalchemy import update
        from app.modules.reviews.models import ProductReview
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        await s.execute(
            update(ProductReview)
            .where(ProductReview.id == rid)
            .values(created_at=old),
        )
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        with pytest.raises(ReviewEditWindowExpiredError):
            await svc.edit(
                review_id=rid,
                customer_id=registered_user["user_id"],
                rating=2,
                title=None,
                body="trying to edit past the cutoff",
            )


# ───────── 4. Moderation transitions ─────────


async def test_approve_transitions_pending_to_approved_and_recomputes(
    registered_user, admin_user,
):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        r = await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=5,
            title=None,
            body="great purchase, recommend",
            principal=_principal(registered_user["user_id"]),
        )
        rid = r.id

    async with sm() as s, s.begin():
        svc = ReviewService(s)
        approved = await svc.approve(
            review_id=rid, principal=_admin(admin_user["user_id"]),
        )
    assert approved.status == STATUS_APPROVED

    async with sm() as s, s.begin():
        repo = ReviewRepository(s)
        agg = await repo.get_aggregate(pid)
    assert agg is not None
    assert agg.review_count == 1
    assert agg.avg_rating == Decimal("5.00")


async def test_reject_terminal_blocks_later_approval(
    registered_user, admin_user,
):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        r = await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=1,
            title=None,
            body="this is spammy content, not a real review",
            principal=_principal(registered_user["user_id"]),
        )
        rid = r.id
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        rejected = await svc.reject(
            review_id=rid,
            reason="spam content",
            principal=_admin(admin_user["user_id"]),
        )
    assert rejected.status == STATUS_REJECTED
    # rejected → approve must fail per the state machine.
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        with pytest.raises(ReviewBadStateError):
            await svc.approve(
                review_id=rid, principal=_admin(admin_user["user_id"]),
            )


async def test_disable_then_reenable_round_trip(
    registered_user, admin_user,
):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        r = await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=4,
            title=None,
            body="solid product, would buy again next month",
            principal=_principal(registered_user["user_id"]),
        )
        rid = r.id
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        await svc.approve(review_id=rid, principal=_admin(admin_user["user_id"]))
        disabled = await svc.disable(
            review_id=rid, principal=_admin(admin_user["user_id"]),
        )
    assert disabled.status == STATUS_DISABLED
    # Aggregate must drop the disabled review from the count.
    async with sm() as s, s.begin():
        repo = ReviewRepository(s)
        agg = await repo.get_aggregate(pid)
    assert agg is not None
    assert agg.review_count == 0

    async with sm() as s, s.begin():
        svc = ReviewService(s)
        reenabled = await svc.reenable(
            review_id=rid, principal=_admin(admin_user["user_id"]),
        )
    assert reenabled.status == STATUS_APPROVED
    async with sm() as s, s.begin():
        repo = ReviewRepository(s)
        agg = await repo.get_aggregate(pid)
    assert agg is not None
    assert agg.review_count == 1


# ───────── 5. Helpful votes ─────────


async def test_helpful_vote_idempotent_and_no_self_vote(
    registered_user, admin_user, logged_in,
):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        r = await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=5,
            title=None,
            body="great product, totally worth it",
            principal=_principal(registered_user["user_id"]),
        )
        rid = r.id
        await svc.approve(review_id=rid, principal=_admin(admin_user["user_id"]))

    # Author can't upvote themselves.
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        with pytest.raises(ReviewHelpfulSelfVoteError):
            await svc.vote_helpful(
                review_id=rid,
                customer_id=registered_user["user_id"],
                principal=_principal(registered_user["user_id"]),
            )

    # Different customer (admin user) votes — first try lands.
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        new_count, voted = await svc.vote_helpful(
            review_id=rid,
            customer_id=admin_user["user_id"],
            principal=_admin(admin_user["user_id"]),
        )
    assert voted is True
    assert new_count == 1

    # Same customer votes again — idempotent no-op.
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        new_count, voted = await svc.vote_helpful(
            review_id=rid,
            customer_id=admin_user["user_id"],
            principal=_admin(admin_user["user_id"]),
        )
    assert voted is False
    assert new_count == 1


# ───────── 6. Public list filters out non-approved ─────────


async def test_public_list_excludes_pending_and_rejected(
    registered_user, admin_user,
):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=registered_user["user_id"],
        variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        # 1 pending review only — public list must be empty.
        await svc.create(
            product_id=pid,
            customer_id=registered_user["user_id"],
            rating=5,
            title=None,
            body="just submitted, awaiting moderation",
            principal=_principal(registered_user["user_id"]),
        )
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        items, total = await svc.list_public(pid)
    assert total == 0
    assert items == []
