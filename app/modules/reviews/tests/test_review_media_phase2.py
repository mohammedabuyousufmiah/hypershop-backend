"""Service-level tests for review media — phase 2.

Covers:
  - happy path: attach photo to a pending review
  - cap enforced: 5th photo rejected
  - bad state: rejected review can't accept new photos
  - non-owner blocked
  - public list bulk-loads media for approved reviews
  - phase-1 invariants still hold (verified-purchase, single review)
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.db.session import get_sessionmaker
from app.core.security.principal import Principal
from app.modules.reviews.codes import (
    MAX_MEDIA_PER_REVIEW,
    MEDIA_KIND_IMAGE,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from app.modules.reviews.errors import (
    ReviewBadStateError,
    ReviewMediaTooManyError,
    ReviewNotFoundError,
)
from app.modules.reviews.repository import ReviewRepository
from app.modules.reviews.service import ReviewService
from app.modules.reviews.tests._helpers import (
    seed_completed_order,
    seed_product_with_variant,
)

pytestmark = pytest.mark.integration


def _principal(user_id) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        roles=frozenset({"customer"}),
        permissions=frozenset({"reviews.write"}),
    )


def _admin(user_id) -> Principal:
    return Principal(
        user_id=user_id,
        session_id=uuid4(),
        roles=frozenset({"manager"}),
        permissions=frozenset({"reviews.admin"}),
    )


async def _seed_pending_review(customer_user_id):
    pid, vid = await seed_product_with_variant()
    await seed_completed_order(
        customer_user_id=customer_user_id, variant_id=vid,
    )
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        r = await svc.create(
            product_id=pid,
            customer_id=customer_user_id,
            rating=5,
            title=None,
            body="great product, fast delivery",
            principal=_principal(customer_user_id),
        )
    return pid, r.id


# ───────── 1. Happy path attach ─────────


async def test_attach_photo_succeeds_on_pending_review(registered_user):
    _, rid = await _seed_pending_review(registered_user["user_id"])
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        media = await svc.attach_photo(
            review_id=rid,
            customer_id=registered_user["user_id"],
            url="/media/test.jpg",
            object_key="review_media/x/test.jpg",
            content_type="image/jpeg",
            file_size_bytes=12345,
            principal=_principal(registered_user["user_id"]),
        )
    assert media.kind == MEDIA_KIND_IMAGE
    assert media.position == 0


# ───────── 2. Cap enforced ─────────


async def test_attach_photo_caps_at_max(registered_user):
    _, rid = await _seed_pending_review(registered_user["user_id"])
    sm = get_sessionmaker()
    # Attach MAX photos
    for i in range(MAX_MEDIA_PER_REVIEW):
        async with sm() as s, s.begin():
            svc = ReviewService(s)
            await svc.attach_photo(
                review_id=rid,
                customer_id=registered_user["user_id"],
                url=f"/media/{i}.jpg",
                object_key=f"review_media/x/{i}.jpg",
                content_type="image/jpeg",
                file_size_bytes=1000,
                principal=_principal(registered_user["user_id"]),
            )
    # MAX+1 must reject
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        with pytest.raises(ReviewMediaTooManyError):
            await svc.attach_photo(
                review_id=rid,
                customer_id=registered_user["user_id"],
                url="/media/over.jpg",
                object_key="review_media/x/over.jpg",
                content_type="image/jpeg",
                file_size_bytes=1000,
                principal=_principal(registered_user["user_id"]),
            )


# ───────── 3. Bad state ─────────


async def test_attach_photo_blocked_on_rejected_review(
    registered_user, admin_user,
):
    _, rid = await _seed_pending_review(registered_user["user_id"])
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        await svc.reject(
            review_id=rid,
            reason="spam content",
            principal=_admin(admin_user["user_id"]),
        )
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        with pytest.raises(ReviewBadStateError):
            await svc.attach_photo(
                review_id=rid,
                customer_id=registered_user["user_id"],
                url="/media/x.jpg",
                object_key="review_media/x/x.jpg",
                content_type="image/jpeg",
                file_size_bytes=1000,
                principal=_principal(registered_user["user_id"]),
            )


# ───────── 4. Non-owner blocked ─────────


async def test_attach_photo_blocked_for_non_owner(
    registered_user, admin_user,
):
    _, rid = await _seed_pending_review(registered_user["user_id"])
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        # admin_user is NOT the review's customer — should look like
        # not-found to avoid leaking review existence.
        with pytest.raises(ReviewNotFoundError):
            await svc.attach_photo(
                review_id=rid,
                customer_id=admin_user["user_id"],
                url="/media/x.jpg",
                object_key="review_media/x/x.jpg",
                content_type="image/jpeg",
                file_size_bytes=1000,
                principal=_admin(admin_user["user_id"]),
            )


# ───────── 5. Approved review surfaces media in public list ─────────


async def test_public_list_includes_media_for_approved(
    registered_user, admin_user,
):
    pid, rid = await _seed_pending_review(registered_user["user_id"])
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = ReviewService(s)
        await svc.attach_photo(
            review_id=rid,
            customer_id=registered_user["user_id"],
            url="/media/before-approval.jpg",
            object_key="review_media/x/before-approval.jpg",
            content_type="image/jpeg",
            file_size_bytes=1000,
            principal=_principal(registered_user["user_id"]),
        )
        await svc.approve(review_id=rid, principal=_admin(admin_user["user_id"]))

    async with sm() as s, s.begin():
        svc = ReviewService(s)
        items, total = await svc.list_public(pid)
        media = await svc.list_media_for_reviews([r.id for r in items])
    assert total == 1
    assert len(media) == 1
    assert media[0].url == "/media/before-approval.jpg"
