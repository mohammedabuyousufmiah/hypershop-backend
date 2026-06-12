"""Business-logic layer for product_videos.

Two responsibilities:

1. Owns the upload → process → review → approve/reject/disable state
   machine and the audit trail attached to each transition.
2. Builds the public response shape from already-resolved URLs that
   the worker stores at the end of FFmpeg processing.

Long-running work (FFmpeg) is NOT done here — :mod:`.jobs` picks up
``uploaded`` rows and transitions them through ``processing`` →
``ready_for_review`` out-of-band. The api process never blocks on
ffmpeg.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.security.principal import Principal
from app.modules.catalog.repository import ProductRepository
from app.modules.product_videos.codes import (
    ACTION_VIDEO_APPROVED,
    ACTION_VIDEO_DELETED,
    ACTION_VIDEO_DISABLED,
    ACTION_VIDEO_REENABLED,
    ACTION_VIDEO_REJECTED,
    ACTION_VIDEO_REOPENED,
    ACTION_VIDEO_UPLOADED,
    DEDUPE_EVENTS,
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_READY_FOR_REVIEW,
    STATUS_REJECTED,
    STATUS_UPLOADED,
)
from app.modules.product_videos.errors import (
    ProductVideoBadStateError,
    ProductVideoNotFoundError,
)
from app.modules.product_videos.models import ProductVideo
from app.modules.product_videos.repository import (
    ProductVideoRepository,
    VideoEventRepository,
)
from app.modules.product_videos.schemas import (
    AdminProductVideo,
    PublicProductVideo,
)


def _to_public(v: ProductVideo) -> PublicProductVideo:
    """Build the public response — runs only on approved videos."""
    return PublicProductVideo(
        id=v.id,
        product_id=v.product_id,
        title=v.title,
        hls_url=v.hls_url or "",
        thumbnail_url=v.thumbnail_url,
        duration_seconds=v.duration_seconds,
    )


def _to_admin(v: ProductVideo) -> AdminProductVideo:
    return AdminProductVideo(
        id=v.id,
        product_id=v.product_id,
        seller_id=v.seller_id,
        title=v.title,
        status=v.status,
        raw_object_key=v.raw_object_key,
        hls_url=v.hls_url,
        thumbnail_url=v.thumbnail_url,
        duration_seconds=v.duration_seconds,
        file_size_bytes=v.file_size_bytes,
        rejection_reason=v.rejection_reason,
        processing_error=v.processing_error,
        created_at=v.created_at,
        updated_at=v.updated_at,
        approved_at=v.approved_at,
        disabled_at=v.disabled_at,
        reopened_at=v.reopened_at,
    )


class ProductVideoService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProductVideoRepository(session)
        self.events = VideoEventRepository(session)
        self.products = ProductRepository(session)

    # ---- public ----

    async def list_public(
        self, *, product_id: UUID, limit: int = 5,
    ) -> list[PublicProductVideo]:
        rows = await self.repo.list_for_product_public(product_id, limit=limit)
        return [_to_public(r) for r in rows if r.hls_url]

    async def record_event(
        self,
        *,
        video_id: UUID,
        event_type: str,
        session_id: str | None,
        customer_id: UUID | None,
        watch_seconds: int,
    ) -> None:
        v = await self.repo.get(video_id)
        if v is None or v.status != STATUS_APPROVED:
            # Quietly ignore — moderation may have pulled the video
            # mid-session and we don't want a 404 to crash the page.
            return
        if event_type in DEDUPE_EVENTS and await self.events.already_recorded(
            video_id=video_id,
            session_id=session_id,
            event_type=event_type,
        ):
            return
        await self.events.record(
            video_id=video_id,
            product_id=v.product_id,
            event_type=event_type,
            session_id=session_id,
            customer_id=customer_id,
            watch_seconds=watch_seconds,
        )
        # Increment AFTER the row is written — counter must reflect
        # rows that actually landed in video_events, not the request
        # rate (which would include silent drops for non-approved
        # videos).
        from app.modules.product_videos.metrics import event_post_total

        event_post_total.labels(event_type=event_type).inc()

    # ---- admin ----

    async def register_upload(
        self,
        *,
        video_id: UUID,
        product_id: UUID,
        seller_id: UUID | None,
        title: str | None,
        raw_object_key: str,
        file_size_bytes: int,
        principal: Principal,
    ) -> AdminProductVideo:
        product = await self.products.get(product_id)
        if product is None:
            raise NotFoundError("Product not found.")

        v = await self.repo.create(
            id=video_id,
            product_id=product_id,
            seller_id=seller_id,
            title=(title or None),
            status=STATUS_UPLOADED,
            raw_object_key=raw_object_key,
            file_size_bytes=file_size_bytes,
        )
        await record_audit(
            actor=principal,
            action=ACTION_VIDEO_UPLOADED,
            resource_type="product_video",
            resource_id=v.id,
            metadata={
                "product_id": str(product_id),
                "seller_id": str(seller_id) if seller_id else None,
                "file_size_bytes": file_size_bytes,
                "raw_object_key": raw_object_key,
            },
        )
        return _to_admin(v)

    async def list_admin(
        self,
        *,
        status: str | None,
        product_id: UUID | None,
        seller_id: UUID | None,
        offset: int,
        limit: int,
    ) -> tuple[list[AdminProductVideo], int]:
        rows, total = await self.repo.list_admin(
            status=status,
            product_id=product_id,
            seller_id=seller_id,
            offset=offset,
            limit=limit,
        )
        return [_to_admin(r) for r in rows], total

    async def list_pending(
        self, *, offset: int, limit: int,
    ) -> tuple[list[AdminProductVideo], int]:
        """Convenience wrapper around :meth:`ProductVideoRepository.list_pending`.

        Always status=``ready_for_review``; oldest first (FIFO by
        upload time) so the admin queue clears in upload order.
        """
        rows, total = await self.repo.list_pending(offset=offset, limit=limit)
        return [_to_admin(r) for r in rows], total

    async def list_recently_moderated(
        self,
        *,
        offset: int,
        limit: int,
        statuses: list[str] | None = None,
    ) -> tuple[list[AdminProductVideo], int]:
        """Most-recently-changed rows first, optionally filtered by status.

        Use this for "recently moderated" admin views and audit dashboards
        — sorted on ``updated_at`` (which bumps every transition).
        """
        rows, total = await self.repo.list_recently_moderated(
            offset=offset, limit=limit, statuses=statuses,
        )
        return [_to_admin(r) for r in rows], total

    async def get_admin(self, video_id: UUID) -> AdminProductVideo:
        v = await self.repo.get(video_id)
        if v is None:
            raise ProductVideoNotFoundError()
        return _to_admin(v)

    async def update_title(
        self,
        *,
        video_id: UUID,
        title: str | None,
    ) -> AdminProductVideo:
        v = await self.repo.get(video_id)
        if v is None:
            raise ProductVideoNotFoundError()
        await self.repo.update_title(video_id=video_id, title=title)
        refreshed = await self.repo.get(video_id)
        assert refreshed is not None
        # Reload expired attrs (post-UPDATE) in the async context so
        # _to_admin's reads don't fire a lazy load → MissingGreenlet.
        await self.session.refresh(refreshed)
        return _to_admin(refreshed)

    async def approve(
        self, *, video_id: UUID, principal: Principal,
    ) -> AdminProductVideo:
        v = await self.repo.get(video_id)
        if v is None:
            raise ProductVideoNotFoundError()
        # Strict: only ready_for_review can be approved. Rejected /
        # disabled rows are terminal — admin must delete + re-upload
        # to restart the flow (or use /reenable to flip disabled back
        # into ready_for_review for another moderation pass).
        if v.status != STATUS_READY_FOR_REVIEW:
            raise ProductVideoBadStateError(
                f"Video must be 'ready_for_review' to approve "
                f"(current: {v.status}).",
                details={"current_status": v.status},
            )
        # Both URLs are populated in lock-step at the end of FFmpeg
        # processing (see jobs._process_one), so a missing one means
        # something stomped on the row outside the normal pipeline.
        if not v.hls_url:
            raise ProductVideoBadStateError(
                "Video has no HLS output yet.",
                details={"missing": "hls_url"},
            )
        if not v.thumbnail_url:
            raise ProductVideoBadStateError(
                "Video has no thumbnail yet.",
                details={"missing": "thumbnail_url"},
            )
        # Per-product approval cap. Counted FROM the current row's
        # product_id (not the principal) so the cap is enforced even
        # when an attacker tries to flip a video onto a different
        # product — the FK still holds, the count is still right.
        cap = get_settings().product_video_max_approved_per_product
        already_approved = await self.repo.count_approved_for_product(v.product_id)
        if already_approved >= cap:
            from app.modules.product_videos.metrics import (
                approve_cap_hit_total,
            )
            approve_cap_hit_total.inc()
            raise ProductVideoBadStateError(
                f"Product already has {already_approved} approved video(s); "
                f"cap is {cap}. Disable or reject an existing one first.",
                details={
                    "product_id": str(v.product_id),
                    "approved_count": already_approved,
                    "cap": cap,
                },
            )
        await self.repo.approve(video_id=video_id)
        await record_audit(
            actor=principal,
            action=ACTION_VIDEO_APPROVED,
            resource_type="product_video",
            resource_id=video_id,
            metadata={"product_id": str(v.product_id)},
        )
        refreshed = await self.repo.get(video_id)
        assert refreshed is not None
        # Reload expired attrs (post-UPDATE) in the async context so
        # _to_admin's reads don't fire a lazy load → MissingGreenlet.
        await self.session.refresh(refreshed)
        return _to_admin(refreshed)

    async def reject(
        self, *, video_id: UUID, reason: str, principal: Principal,
    ) -> AdminProductVideo:
        v = await self.repo.get(video_id)
        if v is None:
            raise ProductVideoNotFoundError()
        # Strict per the locked state machine — reject is only a
        # FIRST-review decision. Approved videos are taken down via
        # disable (reversible) or DELETE (terminal); we do NOT let
        # admins flip approved → rejected because rejected is itself
        # near-terminal (only /reopen can revive it).
        if v.status != STATUS_READY_FOR_REVIEW:
            raise ProductVideoBadStateError(
                f"Video must be 'ready_for_review' to reject "
                f"(current: {v.status}). Use /disable for approved "
                f"videos or DELETE for terminal removal.",
                details={"current_status": v.status},
            )
        await self.repo.reject(video_id=video_id, reason=reason)
        await record_audit(
            actor=principal,
            action=ACTION_VIDEO_REJECTED,
            resource_type="product_video",
            resource_id=video_id,
            metadata={
                "product_id": str(v.product_id),
                "reason": reason,
            },
        )
        refreshed = await self.repo.get(video_id)
        assert refreshed is not None
        # Reload expired attrs (post-UPDATE) in the async context so
        # _to_admin's reads don't fire a lazy load → MissingGreenlet.
        await self.session.refresh(refreshed)
        return _to_admin(refreshed)

    async def disable(
        self,
        *,
        video_id: UUID,
        reason: str | None,
        principal: Principal,
    ) -> AdminProductVideo:
        v = await self.repo.get(video_id)
        if v is None:
            raise ProductVideoNotFoundError()
        if v.status != STATUS_APPROVED:
            raise ProductVideoBadStateError(
                f"Only 'approved' videos can be disabled (current: {v.status}).",
            )
        await self.repo.disable(video_id=video_id, reason=reason)
        await record_audit(
            actor=principal,
            action=ACTION_VIDEO_DISABLED,
            resource_type="product_video",
            resource_id=video_id,
            metadata={
                "product_id": str(v.product_id),
                "reason": reason,
            },
        )
        refreshed = await self.repo.get(video_id)
        assert refreshed is not None
        # Reload expired attrs (post-UPDATE) in the async context so
        # _to_admin's reads don't fire a lazy load → MissingGreenlet.
        await self.session.refresh(refreshed)
        return _to_admin(refreshed)

    async def reenable(
        self, *, video_id: UUID, principal: Principal,
    ) -> AdminProductVideo:
        v = await self.repo.get(video_id)
        if v is None:
            raise ProductVideoNotFoundError()
        # ``reenable`` is for DISABLED rows only. ``rejected`` rows
        # have their own controlled re-entry path via ``reopen()`` —
        # disabling and rejecting are different decisions and we
        # don't want admins accidentally laundering a content-violation
        # rejection back into the queue through the disabled path.
        if v.status != STATUS_DISABLED:
            raise ProductVideoBadStateError(
                f"Only 'disabled' videos can be re-enabled (current: {v.status}). "
                f"Rejected videos must use /reopen.",
                details={"current_status": v.status},
            )
        await self.repo.reenable(video_id=video_id)
        await record_audit(
            actor=principal,
            action=ACTION_VIDEO_REENABLED,
            resource_type="product_video",
            resource_id=video_id,
            metadata={"product_id": str(v.product_id)},
        )
        refreshed = await self.repo.get(video_id)
        assert refreshed is not None
        # Reload expired attrs (post-UPDATE) in the async context so
        # _to_admin's reads don't fire a lazy load → MissingGreenlet.
        await self.session.refresh(refreshed)
        return _to_admin(refreshed)

    async def reopen(
        self,
        *,
        video_id: UUID,
        reason: str,
        principal: Principal,
    ) -> AdminProductVideo:
        """Flip a REJECTED video back to ``ready_for_review`` for re-review.

        Strict guards:
          * status MUST be ``rejected`` — rejected is the only source
            state. Approved / disabled / failed cannot be reopened
            (each has its own, semantically distinct, recovery path).
          * caller must hold the admin perm (enforced at the router).
          * ``reason`` is mandatory (Pydantic enforces 3..2000 chars).

        The row's existing ``rejection_reason`` is PRESERVED — the
        next reviewer can see why the original reject happened. The
        audit log captures the reopen reason + a snapshot of the old
        rejection_reason so the history is recoverable even if the
        row is later re-rejected with a new reason.
        """
        v = await self.repo.get(video_id)
        if v is None:
            raise ProductVideoNotFoundError()
        if v.status != STATUS_REJECTED:
            raise ProductVideoBadStateError(
                f"Only 'rejected' videos can be reopened "
                f"(current: {v.status}).",
                details={"current_status": v.status},
            )
        previous_rejection_reason = v.rejection_reason
        await self.repo.reopen(video_id=video_id)
        await record_audit(
            actor=principal,
            action=ACTION_VIDEO_REOPENED,
            resource_type="product_video",
            resource_id=video_id,
            metadata={
                "product_id": str(v.product_id),
                "reopen_reason": reason,
                # Snapshot — the row keeps this string in the
                # ``rejection_reason`` column too, but stamping it on
                # the audit row guards against later overwrites.
                "previous_rejection_reason": previous_rejection_reason,
            },
        )
        refreshed = await self.repo.get(video_id)
        assert refreshed is not None
        # Reload expired attrs (post-UPDATE) in the async context so
        # _to_admin's reads don't fire a lazy load → MissingGreenlet.
        await self.session.refresh(refreshed)
        return _to_admin(refreshed)

    async def delete(
        self, *, video_id: UUID, principal: Principal,
    ) -> None:
        v = await self.repo.get(video_id)
        if v is None:
            raise ProductVideoNotFoundError()
        product_id = v.product_id
        await self.repo.delete(video_id)
        await record_audit(
            actor=principal,
            action=ACTION_VIDEO_DELETED,
            resource_type="product_video",
            resource_id=video_id,
            metadata={"product_id": str(product_id)},
        )
