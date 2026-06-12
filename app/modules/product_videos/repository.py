"""Async SQLAlchemy repository for product_videos tables."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.product_videos.codes import (
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_PROCESSING,
    STATUS_READY_FOR_REVIEW,
    STATUS_REJECTED,
    STATUS_UPLOADED,
)
from app.modules.product_videos.models import ProductVideo, VideoEvent


class ProductVideoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- create / read ----

    async def create(self, **fields: object) -> ProductVideo:
        v = ProductVideo(**fields)
        self.session.add(v)
        await self.session.flush()
        return v

    async def get(self, video_id: UUID) -> ProductVideo | None:
        return await self.session.get(ProductVideo, video_id)

    async def list_for_product_public(
        self, product_id: UUID, *, limit: int = 5,
    ) -> Sequence[ProductVideo]:
        """Approved videos for a product, newest-approved first.

        Sort key is ``approved_at`` (the moment moderation said yes),
        NOT ``updated_at`` (which would let title-edits / reenables /
        reopens shuffle the customer-facing order). The customer-
        facing publish moment IS the approval moment, not the upload
        moment — sellers might wait days between upload and approval,
        and a freshly approved older upload should still rank above a
        same-day upload that's still pending.

        Tiebreaker: ``created_at DESC`` for two videos approved in
        the same instant (bulk-approve via SQL, parallel admin
        clicks). Without the tiebreaker the order would be
        non-deterministic across DB roundtrips, which would make the
        public list flicker between page loads.

        ``approved_at NULLS LAST`` is defensive: any approved row
        that somehow lost the timestamp (manual SQL fix, replication
        skew) ranks last instead of crashing the page; the
        ``created_at`` tiebreaker keeps even the NULL bucket sorted
        deterministically.
        """
        stmt = (
            select(ProductVideo)
            .where(
                ProductVideo.product_id == product_id,
                ProductVideo.status == STATUS_APPROVED,
            )
            .order_by(
                ProductVideo.approved_at.desc().nullslast(),
                ProductVideo.created_at.desc(),
            )
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_pending(
        self, *, offset: int, limit: int,
    ) -> tuple[Sequence[ProductVideo], int]:
        """Videos waiting for admin review — oldest UPLOADED first.

        Sort key is ``created_at ASC`` (FIFO by upload time), not
        ``updated_at`` — a video uploaded yesterday but transitioned
        to ready_for_review today should sit AHEAD of one uploaded
        today, because the upload-time queue position is what the
        seller experiences as "fairness".

        ``ready_for_review`` only; ``failed`` rows surface separately
        via ``list_admin(status='failed', ...)`` because the action
        on those is "fix the source", not "approve / reject".
        """
        cond = ProductVideo.status == STATUS_READY_FOR_REVIEW
        total = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(ProductVideo)
                    .where(cond),
                )
            ).scalar_one()
        )
        stmt = (
            select(ProductVideo)
            .where(cond)
            .order_by(ProductVideo.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    async def list_recently_moderated(
        self,
        *,
        offset: int,
        limit: int,
        statuses: Sequence[str] | None = None,
    ) -> tuple[Sequence[ProductVideo], int]:
        """Rows ordered by most-recent moderation action first.

        Sort key is ``updated_at DESC`` — this is the ONE place where
        ``updated_at`` is the right answer, because the question being
        asked is literally "what changed most recently?".

        ``statuses`` filter is optional. The admin UI typically passes
        ``{"approved", "rejected", "disabled"}`` to limit the feed to
        actual moderation outcomes (excluding pipeline-state churn
        from the worker like uploaded → processing → ready_for_review).
        Pass ``None`` to include every row.
        """
        base = select(ProductVideo)
        count_base = select(func.count()).select_from(ProductVideo)
        if statuses:
            base = base.where(ProductVideo.status.in_(statuses))
            count_base = count_base.where(ProductVideo.status.in_(statuses))

        total = int((await self.session.execute(count_base)).scalar_one())
        stmt = (
            base.order_by(ProductVideo.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, total

    async def count_approved_for_product(self, product_id: UUID) -> int:
        """Count rows with status='approved' for a single product.

        Used by the approval flow to enforce
        ``settings.product_video_max_approved_per_product``.
        """
        stmt = (
            select(func.count())
            .select_from(ProductVideo)
            .where(
                ProductVideo.product_id == product_id,
                ProductVideo.status == STATUS_APPROVED,
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_admin(
        self,
        *,
        status: str | None,
        product_id: UUID | None,
        seller_id: UUID | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[ProductVideo], int]:
        cond = []
        if status is not None:
            cond.append(ProductVideo.status == status)
        if product_id is not None:
            cond.append(ProductVideo.product_id == product_id)
        if seller_id is not None:
            cond.append(ProductVideo.seller_id == seller_id)
        base = select(ProductVideo)
        if cond:
            base = base.where(*cond)
        total = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery()),
            )
        ).scalar_one()
        stmt = (
            base.order_by(ProductVideo.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, int(total)

    async def claim_specific_for_processing(
        self, video_id: UUID,
    ) -> ProductVideo | None:
        """Atomically transition ONE specific row from uploaded → processing.

        Used by the direct-dispatch path: when the API enqueues
        ``process_product_video(video_id)`` immediately after an upload,
        the worker calls this to take exclusive ownership of that row.

        Returns ``None`` if the row is gone, in a different status, or
        another worker already has the lock — in any of those cases the
        caller should silently no-op (the cron safety-net or the lock
        holder will do the work).
        """
        stmt = (
            select(ProductVideo)
            .where(
                ProductVideo.id == video_id,
                ProductVideo.status == STATUS_UPLOADED,
            )
            .with_for_update(skip_locked=True)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        row.status = STATUS_PROCESSING
        await self.session.flush()
        return row

    async def claim_one_for_processing(self) -> ProductVideo | None:
        """Atomically pick the oldest 'uploaded' video and mark it processing.

        Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so multiple workers
        can poll without stepping on each other. Returns ``None`` if
        the queue is empty.
        """
        stmt = (
            select(ProductVideo)
            .where(ProductVideo.status == STATUS_UPLOADED)
            .order_by(ProductVideo.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        row.status = STATUS_PROCESSING
        await self.session.flush()
        return row

    # ---- mutations ----

    async def mark_ready_for_review(
        self,
        *,
        video_id: UUID,
        hls_url: str,
        thumbnail_url: str,
        duration_seconds: int,
    ) -> None:
        await self.session.execute(
            update(ProductVideo)
            .where(ProductVideo.id == video_id)
            .values(
                status=STATUS_READY_FOR_REVIEW,
                hls_url=hls_url,
                thumbnail_url=thumbnail_url,
                duration_seconds=duration_seconds,
                processing_error=None,
            )
        )

    async def mark_failed(
        self,
        *,
        video_id: UUID,
        error: str,
    ) -> None:
        await self.session.execute(
            update(ProductVideo)
            .where(ProductVideo.id == video_id)
            .values(status="failed", processing_error=error[:8000])
        )

    async def approve(self, *, video_id: UUID) -> None:
        # ``approved_at`` is set to server-side ``now() AT TIME ZONE
        # 'UTC'`` so it can't drift between API process and DB. The
        # public list query orders on this column, so any clock skew
        # would directly distort ranking.
        await self.session.execute(
            update(ProductVideo)
            .where(ProductVideo.id == video_id)
            .values(
                status=STATUS_APPROVED,
                rejection_reason=None,
                approved_at=text("(now() AT TIME ZONE 'UTC')"),
            )
        )

    async def reject(self, *, video_id: UUID, reason: str) -> None:
        await self.session.execute(
            update(ProductVideo)
            .where(ProductVideo.id == video_id)
            .values(status=STATUS_REJECTED, rejection_reason=reason)
        )

    async def disable(self, *, video_id: UUID, reason: str | None) -> None:
        await self.session.execute(
            update(ProductVideo)
            .where(ProductVideo.id == video_id)
            .values(
                status=STATUS_DISABLED,
                rejection_reason=reason,
                disabled_at=text("(now() AT TIME ZONE 'UTC')"),
            )
        )

    async def reenable(self, *, video_id: UUID) -> None:
        # A re-enabled video drops back to ready_for_review so a human
        # has to look at it before it goes public again. ``reenable``
        # is for ``disabled`` rows only; rejected rows go through the
        # separate ``reopen()`` path which preserves the original
        # rejection_reason for audit visibility.
        await self.session.execute(
            update(ProductVideo)
            .where(ProductVideo.id == video_id)
            .values(status=STATUS_READY_FOR_REVIEW, rejection_reason=None)
        )

    async def reopen(self, *, video_id: UUID) -> None:
        """Flip rejected → ready_for_review WITHOUT clearing rejection_reason.

        Preserving the old reason matters because the next reviewer
        needs to see why the original reject happened — they're
        deciding whether the rejection was a mistake or whether the
        seller's content actually needs DELETE + re-upload. The audit
        log captures the reopen-reason separately; the row itself
        keeps the historical rejection-reason string intact.

        ``reopened_at`` is stamped so analytics can answer "how often
        does an admin reverse a rejection?". A repeated reject →
        reopen cycle overwrites this column each time — ``reopened_at``
        always reflects the LAST reopen.
        """
        await self.session.execute(
            update(ProductVideo)
            .where(ProductVideo.id == video_id)
            .values(
                status=STATUS_READY_FOR_REVIEW,
                reopened_at=text("(now() AT TIME ZONE 'UTC')"),
            )
        )

    async def update_title(self, *, video_id: UUID, title: str | None) -> None:
        await self.session.execute(
            update(ProductVideo)
            .where(ProductVideo.id == video_id)
            .values(title=title)
        )

    async def delete(self, video_id: UUID) -> None:
        v = await self.get(video_id)
        if v is None:
            return
        await self.session.delete(v)


class VideoEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        video_id: UUID,
        product_id: UUID,
        event_type: str,
        session_id: str | None,
        customer_id: UUID | None,
        watch_seconds: int,
    ) -> VideoEvent:
        e = VideoEvent(
            video_id=video_id,
            product_id=product_id,
            event_type=event_type,
            session_id=session_id,
            customer_id=customer_id,
            watch_seconds=watch_seconds,
        )
        self.session.add(e)
        await self.session.flush()
        return e

    async def already_recorded(
        self,
        *,
        video_id: UUID,
        session_id: str | None,
        event_type: str,
    ) -> bool:
        """Cheap idempotency check for fire-once events.

        Returns False when ``session_id`` is None — anonymous server-
        side fires can't be deduped without a session token, which is
        the right answer for ATC/Buy attribution events that shouldn't
        be deduped anyway.
        """
        if session_id is None:
            return False
        stmt = (
            select(func.count())
            .select_from(VideoEvent)
            .where(
                VideoEvent.video_id == video_id,
                VideoEvent.session_id == session_id,
                VideoEvent.event_type == event_type,
            )
        )
        n = int((await self.session.execute(stmt)).scalar_one())
        return n > 0
