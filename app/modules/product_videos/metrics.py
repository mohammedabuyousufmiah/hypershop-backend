"""Prometheus metrics for the product_videos pipeline.

This module is **import-only** for instrumentation: importing it
defines + registers the 10 metrics promised in
``docs/MONITORING_MODULE_35.md`` Section 4 against the project
registry. Code paths in jobs / storage / service / router import the
specific metric they want to increment / observe and call it
directly; nothing else routes through this module.

Two flavours of metric live here:

1. **Push metrics** — Counter / Histogram instances incremented from
   code paths (e.g. every Bunny upload error, every FFmpeg run
   completion). These are the bulk of the surface.

2. **Pull metrics** — DB-derived gauges. The
   :class:`PipelineStateCollector` runs four small SQL queries on
   every Prometheus scrape and yields current pipeline state. This is
   cheaper than maintaining application-side state mirrors of every
   row transition.

The module is loaded once from ``app/main.py`` (side-effect import
after all other module wiring). It is NOT loaded by the worker
container — worker has its own metric story in a future iteration
(see Section 7 of the monitoring doc).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import select, text

from app.core.db.session import get_sessionmaker
from app.core.logging import get_logger
from app.core.metrics import (
    CollectorBase,
    GaugeMetricFamily,
    make_counter,
    make_histogram,
    register_collector,
)

_log = get_logger("hypershop.product_videos.metrics")


# ────────────── push counters / histograms ──────────────

# FFmpeg processing time. Buckets cover 5 s (small clip, fast worker)
# through 300 s (worker hard timeout). Anything > 300 s would be a
# stuck job — the histogram's +Inf bucket catches those for alerting.
ffmpeg_duration_seconds = make_histogram(
    "product_video_ffmpeg_duration_seconds",
    "Time taken by ffmpeg to process one product video, end-to-end "
    "(probe + thumbnail + 720p + 360p HLS + Bunny upload). Recorded "
    "by jobs._process_one once per successful pipeline run. Failed "
    "runs are NOT recorded — see product_video_failed_total instead.",
    buckets=(5, 10, 20, 30, 45, 60, 90, 120, 180, 300, float("inf")),
)

failed_total = make_counter(
    "product_video_failed_total",
    "Count of pipeline runs that ended in status=failed. Incremented "
    "from jobs._record_failure once per terminal failure (FFmpeg "
    "crash, ffprobe rejection, missing raw, unexpected exception).",
)

bunny_upload_errors_total = make_counter(
    "product_video_bunny_upload_errors_total",
    "Count of Bunny Storage PUT requests that returned non-2xx. "
    "Incremented from storage.bunny_upload_public_file when the "
    "BunnyUploadError is raised. High rate = check Bunny dashboard "
    "or rotate BUNNY_STORAGE_ACCESS_KEY.",
    labels=["status_code"],
)

# Per-PUT wall-clock latency to Bunny Storage. Buckets are tighter
# at the low end than the generic HTTP histogram because Bunny PUTs
# are I/O-dominated single-segment writes — we want sub-second
# resolution to spot regional carrier latency drift early. The
# ``outcome`` label collapses to two values ("success" / "error")
# so the histogram series count stays at 18 (2 × 9 buckets).
bunny_upload_duration_seconds = make_histogram(
    "product_video_bunny_upload_duration_seconds",
    "Wall-clock time for one Bunny Storage PUT, end-to-end. Recorded "
    "by storage.bunny_upload_public_file regardless of outcome — "
    "label ``outcome`` distinguishes 2xx (``success``) from non-2xx "
    "(``error``). Useful for detecting Bunny edge / network drift "
    "without waiting for an outright failure spike.",
    labels=["outcome"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, float("inf")),
)

r2_upload_errors_total = make_counter(
    "product_video_r2_upload_errors_total",
    "Count of R2 PUT failures during the upload-handler raw-write "
    "path. Incremented from router.upload_product_video on the "
    "exception branch around upload_private_file.",
)

arq_enqueue_failures_total = make_counter(
    "product_video_arq_enqueue_failures_total",
    "Count of times the api process failed to enqueue a "
    "process_product_video job after a successful upload. Worker "
    "cron tick remains a safety net — but a non-zero rate here "
    "means latency to first FFmpeg run grew from ~1s to ~30s.",
)

approve_cap_hit_total = make_counter(
    "product_video_approve_cap_hit_total",
    "Count of /approve calls that returned 409 because the product "
    "already had product_video_max_approved_per_product approved "
    "videos. High rate = sellers banging against cap; consider "
    "raising the cap or surfacing a clearer error.",
)

event_post_total = make_counter(
    "product_video_event_post_total",
    "Count of /event endpoint calls that resulted in a row written "
    "to video_events (i.e. status was approved + dedup didn't "
    "drop). Drops > 50% week-over-week = frontend instrumentation "
    "regression.",
    labels=["event_type"],
)


# ────────────── pull gauges (DB-derived collector) ──────────────

class PipelineStateCollector(CollectorBase):
    """Runs four cheap SQL queries on every Prometheus scrape.

    Yields gauges for:

    - ``product_video_status_count{status=...}`` — distribution of
      rows across the 7 status values
    - ``product_video_uploaded_oldest_seconds`` — age of the oldest
      row stuck in ``uploaded`` (worker-drain SLO indicator)
    - ``product_video_processing_in_flight`` — count of rows in
      ``processing`` (should equal `product_video_max_concurrent_jobs`
      ± 1 in steady state; large = worker stuck)
    - ``product_video_ready_for_review_oldest_seconds`` — age of
      oldest pending moderation row (SLO: < 86400)

    Cheap because every WHERE clause hits the existing
    ``ix_product_videos_status`` / ``ix_product_videos_product_status``
    indexes. Total per-scrape cost on a 100k-row table is <50ms.
    """

    def collect(self) -> Iterator[GaugeMetricFamily]:
        try:
            yield from self._collect_sync()
        except Exception as e:  # noqa: BLE001 — Prometheus must never throw
            # Returning empty on error keeps the scrape going for the
            # other modules' metrics. The error is logged + surfaces
            # as a missing data point on dashboards.
            _log.warning("pipeline_state_collector_failed", error=str(e))
            return

    # NOTE: prometheus_client calls .collect() synchronously, so we
    # use the sync engine path here. The cost is one short-lived
    # blocking SQL call per scrape (typically every 15-60s); fine.
    def _collect_sync(self) -> Iterator[GaugeMetricFamily]:
        # Lazy import — the sync engine is only constructed when the
        # collector actually runs (not at module-import time, which
        # happens during create_app() before the engine config is
        # validated).
        from app.core.config import get_settings
        from sqlalchemy import create_engine

        s = get_settings()
        # Lightweight one-shot engine; pool_pre_ping avoids stale
        # connections on long-quiet stacks.
        engine = create_engine(
            s.database_sync_url,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
        )
        try:
            with engine.connect() as conn:
                # 1. status distribution
                status_gauge = GaugeMetricFamily(
                    "product_video_status_count",
                    "Number of product_videos rows currently in each status.",
                    labels=["status"],
                )
                rows = conn.execute(
                    text(
                        "SELECT status, count(*) FROM product_videos "
                        "GROUP BY status",
                    ),
                ).all()
                seen = {r[0] for r in rows}
                for status, n in rows:
                    status_gauge.add_metric([status], float(n))
                # Emit zero for absent statuses so dashboards don't
                # silently drop the dimension after a status empties.
                for absent in {
                    "uploaded", "processing", "ready_for_review",
                    "approved", "rejected", "disabled", "failed",
                } - seen:
                    status_gauge.add_metric([absent], 0.0)
                yield status_gauge

                # 2. oldest uploaded
                row = conn.execute(
                    text(
                        "SELECT extract(epoch FROM (now() - min(created_at))) "
                        "FROM product_videos WHERE status = 'uploaded'",
                    ),
                ).scalar()
                yield GaugeMetricFamily(
                    "product_video_uploaded_oldest_seconds",
                    "Age in seconds of the oldest row currently in "
                    "status=uploaded. NaN/0 when queue is empty.",
                    value=float(row or 0),
                )

                # 3. processing in flight
                row = conn.execute(
                    text(
                        "SELECT count(*) FROM product_videos "
                        "WHERE status = 'processing'",
                    ),
                ).scalar()
                yield GaugeMetricFamily(
                    "product_video_processing_in_flight",
                    "Count of rows currently mid-FFmpeg.",
                    value=float(row or 0),
                )

                # 4. oldest ready_for_review
                row = conn.execute(
                    text(
                        "SELECT extract(epoch FROM (now() - min(updated_at))) "
                        "FROM product_videos WHERE status = 'ready_for_review'",
                    ),
                ).scalar()
                yield GaugeMetricFamily(
                    "product_video_ready_for_review_oldest_seconds",
                    "Age in seconds of the oldest row awaiting admin "
                    "moderation. SLO target < 86400 (24h).",
                    value=float(row or 0),
                )
        finally:
            engine.dispose()


# Register on import — main.py's side-effect import wires this up.
register_collector(PipelineStateCollector())
