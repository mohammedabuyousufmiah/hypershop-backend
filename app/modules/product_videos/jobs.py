"""ARQ jobs for the product_videos module.

Two cron tasks:

- ``process_pending_videos_job`` (every 30s) — claims rows in
  ``uploaded`` status using ``SELECT ... FOR UPDATE SKIP LOCKED``,
  runs the FFmpeg pipeline, and pushes the output to Bunny Storage
  (or to disk when Bunny isn't configured). Successful rows reach
  ``ready_for_review`` and wait for a human to approve.

- ``cleanup_raw_originals_job`` (daily at 02:15 UTC) — deletes raw
  originals on R2 (or disk) older than
  ``settings.product_video_raw_retention_days`` for rows in a
  terminal state (approved / rejected / disabled). The HLS bundle on
  Bunny is untouched — only the private raw is purged so the storage
  bill doesn't grow forever. ``failed`` rows are NEVER auto-purged so
  an operator can pull the original to diagnose the FFmpeg crash.

Pipeline at a glance, with R2 + Bunny enabled::

    [API process]   PUT raw → R2 (private prefix)
                    DB row → status=uploaded, raw_object_key=<R2 key>

    [worker tick]   claim row → status=processing
                    download from R2 → /tmp/<id>/original.<ext>
                    ffprobe + duration check
                    ffmpeg poster.jpg + 720p/480p HLS → /tmp/<id>/...
                    PUT poster + hls/* → Bunny Storage
                    DB row → status=ready_for_review,
                             hls_url + thumbnail_url = Bunny CDN URL
                    rmtree /tmp/<id>

    [cleanup cron]  every 24h, scan rows older than retention,
                    delete raw_object_key from R2,
                    set raw_object_key=NULL on the row.

Polling instead of ``arq.enqueue_job`` keeps the API process free of
ARQ enqueue plumbing and lets multiple worker pods race via
``SKIP LOCKED`` without a central scheduler.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from app.core.audit import record_audit
from app.core.config import get_settings
from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.core.time import utc_now
from app.modules.product_videos.codes import (
    ACTION_VIDEO_PROCESSED,
    ACTION_VIDEO_PROCESSING_FAILED,
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_REJECTED,
)
from app.modules.product_videos.ffmpeg import (
    FFmpegFailure,
    ffmpeg_available,
    make_poster,
    probe,
    transcode_to_hls,
)
from app.modules.product_videos.metrics import (
    failed_total,
    ffmpeg_duration_seconds,
)
from app.modules.product_videos.models import ProductVideo
from app.modules.product_videos.repository import ProductVideoRepository
from app.modules.product_videos.storage import (
    absolute_path,
    bunny_enabled,
    bunny_public_url,
    bunny_upload_public_file,
    bunny_upload_public_folder,
    delete_object,
    download_private_file,
    public_url_for,
    r2_enabled,
)

_log = get_logger("hypershop.product_videos.jobs")


# =====================================================================
#                   process_pending_videos_job (every 30s)
# =====================================================================


async def process_pending_videos_job(_ctx: dict[str, Any]) -> int:
    """Pick up to N pending videos and run FFmpeg on each.

    Each video runs in its own UnitOfWork so a single bad file can't
    poison the whole tick. Returns the number of videos handled
    (succeeded + failed).
    """
    has_ffmpeg, has_ffprobe = ffmpeg_available()
    if not (has_ffmpeg and has_ffprobe):
        _log.warning(
            "product_video_ffmpeg_unavailable",
            ffmpeg=has_ffmpeg,
            ffprobe=has_ffprobe,
        )
        return 0

    settings = get_settings()
    max_jobs = settings.product_video_max_concurrent_jobs
    handled = 0

    for _ in range(max_jobs):
        # 1. Claim one row → 'processing' (commits immediately so other
        #    workers see the lock release).
        async with UnitOfWork().transactional() as session:
            repo = ProductVideoRepository(session)
            v = await repo.claim_one_for_processing()
            if v is None:
                # Queue empty — return rather than continue, so we don't
                # spin on empty when fewer than max_jobs were enqueued.
                return handled
            video_id = v.id
            product_id = v.product_id
            raw_object_key = v.raw_object_key

        if not raw_object_key:
            await _record_failure(
                video_id=video_id,
                product_id=product_id,
                error="raw_object_key missing on row",
            )
            handled += 1
            continue

        # 2. Run the pipeline outside the DB transaction. This can take
        #    minutes; we don't want a postgres connection held open.
        try:
            duration, hls_url, thumbnail_url = await _process_one(
                video_id=video_id,
                raw_object_key=raw_object_key,
            )
        except FFmpegFailure as e:
            await _record_failure(
                video_id=video_id,
                product_id=product_id,
                error=str(e),
            )
            handled += 1
            continue
        except Exception as e:  # noqa: BLE001 — record + keep ticking
            _log.exception(
                "product_video_unexpected_error",
                video_id=str(video_id),
                error=str(e),
            )
            await _record_failure(
                video_id=video_id,
                product_id=product_id,
                error=f"unexpected: {type(e).__name__}: {e}",
            )
            handled += 1
            continue

        # 3. Persist success in a fresh transaction.
        async with UnitOfWork().transactional() as session:
            repo = ProductVideoRepository(session)
            await repo.mark_ready_for_review(
                video_id=video_id,
                hls_url=hls_url,
                thumbnail_url=thumbnail_url,
                duration_seconds=duration,
            )
            await record_audit(
                actor=None,
                action=ACTION_VIDEO_PROCESSED,
                resource_type="product_video",
                resource_id=video_id,
                metadata={
                    "product_id": str(product_id),
                    "duration_seconds": duration,
                    "hls_url": hls_url,
                    "storage_backend": (
                        "bunny" if bunny_enabled() else "disk"
                    ),
                    "raw_backend": "r2" if r2_enabled() else "disk",
                },
            )
        handled += 1

    return handled


# ---------------------------------------------------------------------
#  Direct-dispatch entry point — called via ARQ enqueue from the API
# ---------------------------------------------------------------------


async def process_product_video(
    _ctx: dict[str, Any],
    video_id_hex: str,
) -> bool:
    """ARQ task — process ONE specific video by id.

    Enqueued by the API right after a successful upload so processing
    starts in <1 s instead of waiting up to 30 s for the cron tick.
    The cron (``process_pending_videos_job``) remains as a safety net
    for any rows that fall through (enqueue failed, worker restart
    mid-job, etc.) — both paths share the same pipeline so the row
    reaches ``ready_for_review`` exactly once either way.

    Returns ``True`` on success, ``False`` if the row was already
    handled by another tick (no-op).

    The function is idempotent — claiming the row uses
    ``SELECT ... FOR UPDATE SKIP LOCKED`` filtered by id + status,
    so enqueueing the same video_id twice is harmless.
    """
    has_ffmpeg, has_ffprobe = ffmpeg_available()
    if not (has_ffmpeg and has_ffprobe):
        _log.warning(
            "product_video_ffmpeg_unavailable",
            ffmpeg=has_ffmpeg,
            ffprobe=has_ffprobe,
        )
        return False

    try:
        video_id = UUID(video_id_hex)
    except ValueError:
        _log.warning(
            "process_product_video_bad_id", video_id_hex=video_id_hex,
        )
        return False

    # 1. Claim the specific row.
    async with UnitOfWork().transactional() as session:
        repo = ProductVideoRepository(session)
        v = await repo.claim_specific_for_processing(video_id)
        if v is None:
            # Already processed / wrong state / locked elsewhere.
            # Safe to drop — the cron is our fallback if the row
            # somehow stayed in uploaded.
            _log.info(
                "process_product_video_skipped",
                video_id=str(video_id),
                reason="not_claimable",
            )
            return False
        product_id = v.product_id
        raw_object_key = v.raw_object_key

    if not raw_object_key:
        await _record_failure(
            video_id=video_id,
            product_id=product_id,
            error="raw_object_key missing on row",
        )
        return False

    # 2. Run the same pipeline the cron uses.
    try:
        duration, hls_url, thumbnail_url = await _process_one(
            video_id=video_id,
            raw_object_key=raw_object_key,
        )
    except FFmpegFailure as e:
        await _record_failure(
            video_id=video_id,
            product_id=product_id,
            error=str(e),
        )
        return False
    except Exception as e:  # noqa: BLE001
        _log.exception(
            "product_video_unexpected_error",
            video_id=str(video_id),
            error=str(e),
        )
        await _record_failure(
            video_id=video_id,
            product_id=product_id,
            error=f"unexpected: {type(e).__name__}: {e}",
        )
        return False

    # 3. Persist success.
    async with UnitOfWork().transactional() as session:
        repo = ProductVideoRepository(session)
        await repo.mark_ready_for_review(
            video_id=video_id,
            hls_url=hls_url,
            thumbnail_url=thumbnail_url,
            duration_seconds=duration,
        )
        await record_audit(
            actor=None,
            action=ACTION_VIDEO_PROCESSED,
            resource_type="product_video",
            resource_id=video_id,
            metadata={
                "product_id": str(product_id),
                "duration_seconds": duration,
                "hls_url": hls_url,
                "storage_backend": (
                    "bunny" if bunny_enabled() else "disk"
                ),
                "raw_backend": "r2" if r2_enabled() else "disk",
                "trigger": "dispatched",
            },
        )
    return True


async def _process_one(
    *,
    video_id,
    raw_object_key: str,
) -> tuple[int, str, str]:
    """See implementation below — wrapped for the histogram observe."""
    started = time.perf_counter()
    try:
        return await _process_one_impl(
            video_id=video_id, raw_object_key=raw_object_key,
        )
    finally:
        # Recording on the success path AND the FFmpegFailure path
        # gives us the realistic worker-time-per-row distribution
        # (failures still consumed worker cycles). Unexpected
        # exceptions also land here — the +Inf bucket catches them.
        ffmpeg_duration_seconds.observe(time.perf_counter() - started)


async def _process_one_impl(
    *,
    video_id,
    raw_object_key: str,
) -> tuple[int, str, str]:
    """Run the full pipeline on one video.

    Returns ``(duration_s, hls_url, thumbnail_url)`` — both URLs are
    absolute and ready to drop into the DB.

    The function picks the storage backends at runtime:
      raw input  → R2 if ``r2_enabled()``, else local disk
      HLS output → Bunny Storage if ``bunny_enabled()``, else local disk
    """
    settings = get_settings()
    if len(raw_object_key) > 1024:
        raise FFmpegFailure("raw_object_key is implausibly long")

    use_r2 = r2_enabled()
    use_bunny = bunny_enabled()

    # Scratch directory — every file FFmpeg touches lives here so we
    # can rmtree the whole thing in one shot, even on the failure path.
    scratch = Path(tempfile.mkdtemp(prefix=f"pv-{video_id.hex[:8]}-"))
    try:
        original = scratch / "original"
        if use_r2:
            download_private_file(raw_object_key, original)
        else:
            disk_original = absolute_path(raw_object_key)
            if not disk_original.is_file():
                raise FFmpegFailure(
                    f"original missing on disk: {raw_object_key}",
                )
            # Symlink into scratch so FFmpeg's input path is uniform.
            shutil.copy2(disk_original, original)

        info = await probe(original)
        if info.duration_seconds > settings.product_video_max_duration_seconds:
            raise FFmpegFailure(
                f"video too long: {info.duration_seconds}s > "
                f"{settings.product_video_max_duration_seconds}s",
            )

        poster_abs = scratch / "poster.jpg"
        await make_poster(input_path=original, output_path=poster_abs)

        hls_dir_abs = scratch / "hls"
        master_abs = await transcode_to_hls(
            input_path=original,
            hls_dir=hls_dir_abs,
            max_height=info.height,
        )

        # Per-video sub-path used both on Bunny and on disk so
        # debugging across backends stays uniform.
        sub = video_id.hex

        if use_bunny:
            # Push poster + the entire hls/ tree to Bunny. Each call
            # returns the customer-facing CDN URL.
            thumbnail_url = bunny_upload_public_file(
                poster_abs,
                f"{sub}/poster.jpg",
                "image/jpeg",
            )
            uploaded = bunny_upload_public_folder(
                hls_dir_abs,
                f"{sub}/hls",
            )
            # The master playlist is the only file at the top of hls/
            # ending in .m3u8 — find its CDN URL in the returned list.
            hls_url = next(
                (u for u in uploaded if u.endswith("/hls/master.m3u8")),
                # Fallback: build from the known sub-path.
                bunny_public_url(f"{sub}/hls/master.m3u8"),
            )
        else:
            # Disk fallback: copy outputs into the on-disk root, then
            # build a server-relative URL via public_url_for.
            from app.modules.product_videos.storage import storage_root

            root = storage_root()
            target_video_dir = root / "by-id" / sub
            target_video_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(poster_abs, target_video_dir / "poster.jpg")
            target_hls_dir = target_video_dir / "hls"
            if target_hls_dir.exists():
                shutil.rmtree(target_hls_dir)
            shutil.copytree(hls_dir_abs, target_hls_dir)
            thumbnail_url = public_url_for(f"by-id/{sub}/poster.jpg")
            hls_url = public_url_for(f"by-id/{sub}/hls/master.m3u8")

        return (
            info.duration_seconds,
            hls_url,
            thumbnail_url,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def _record_failure(
    *,
    video_id,
    product_id,
    error: str,
) -> None:
    failed_total.inc()
    async with UnitOfWork().transactional() as session:
        repo = ProductVideoRepository(session)
        await repo.mark_failed(video_id=video_id, error=error)
        await record_audit(
            actor=None,
            action=ACTION_VIDEO_PROCESSING_FAILED,
            resource_type="product_video",
            resource_id=video_id,
            metadata={
                "product_id": str(product_id),
                # `error` may include ffmpeg stderr — already trimmed
                # to ~4kB by the adapter. Audit log allows free text.
                "error": error[:2000],
            },
        )


# =====================================================================
#                   cleanup_raw_originals_job (daily)
# =====================================================================


async def cleanup_raw_originals_job(_ctx: dict[str, Any]) -> int:
    """Purge raw originals past the configured retention window.

    Only touches videos in a terminal state (approved / rejected /
    disabled). Status='failed' rows are skipped on purpose — ops may
    still need the raw to diagnose the crash.

    Returns the number of raw originals deleted in this tick.

    Idempotent + bounded: caps each tick at 100 deletes so a sudden
    backlog doesn't lock up the worker. The next 24h tick mops up the
    rest.
    """
    settings = get_settings()
    cutoff = utc_now() - timedelta(
        days=settings.product_video_raw_retention_days,
    )
    use_r2 = r2_enabled()
    deleted = 0

    async with UnitOfWork().transactional() as session:
        rows = (
            (
                await session.execute(
                    select(ProductVideo)
                    .where(
                        ProductVideo.status.in_(
                            (
                                STATUS_APPROVED,
                                STATUS_REJECTED,
                                STATUS_DISABLED,
                            ),
                        ),
                        ProductVideo.updated_at < cutoff,
                        ProductVideo.raw_object_key.isnot(None),
                    )
                    .order_by(ProductVideo.updated_at.asc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )

        for v in rows:
            key = v.raw_object_key
            if not key:
                continue
            try:
                if use_r2:
                    delete_object(key)
                else:
                    abs_path = absolute_path(key)
                    if abs_path.is_file():
                        abs_path.unlink()
            except Exception as e:  # noqa: BLE001 — keep mopping
                _log.warning(
                    "raw_original_delete_failed",
                    video_id=str(v.id),
                    raw_object_key=key,
                    error=str(e),
                )
                continue
            await session.execute(
                update(ProductVideo)
                .where(ProductVideo.id == v.id)
                .values(raw_object_key=None)
            )
            deleted += 1

    if deleted:
        _log.info(
            "raw_originals_purged",
            count=deleted,
            cutoff=cutoff.isoformat(),
            backend="r2" if use_r2 else "disk",
        )
    return deleted
