from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron

from app.core.cache import close_redis
from app.core.config import get_settings
from app.core.db.session import close_engine, get_engine
from app.core.events.dispatcher import dispatch_once
from app.core.logging import configure_logging, get_logger
from app.modules.inventory.jobs import (
    expire_overdue_batches_job,
    near_expiry_scan_job,
)
from app.modules.reporting.analytics_jobs import (
    refresh_analytical_materialized_views_job,
)
from app.modules.reporting.jobs import (
    cleanup_expired_files_job,
    run_due_schedules_job,
)
from app.modules.rider_routing.jobs import (
    mark_stale_riders_offline_job,
    prune_old_live_locations_job,
    snapshot_etas_job,
)
from app.modules.rider_wallet.jobs import (
    apply_previous_day_settlement_lock_job,
    expire_carry_forwards_job,
)
from app.modules.supplier_payments.jobs import (
    recompute_all_recommendations_job,
)
from app.modules.search.jobs import search_index_rebuild_job
from app.modules.product_videos.jobs import (
    cleanup_raw_originals_job,
    process_pending_videos_job,
    process_product_video,
)
from app.modules.ads.jobs import (
    recompute_quality_scores_job,
    reset_daily_budgets_job,
)
from app.modules.cart_recovery.jobs import (
    scan_abandoned_carts_job,
    scan_dormant_customers_job,
)
from app.modules.disputes.jobs import auto_escalate_overdue_disputes_job
from app.modules.seller_rating.jobs import recompute_all_seller_ratings_job
from app.modules.customer_segments.jobs import (
    materialize_all_segments_job,
    recompute_all_rfm_scores_job,
)
from app.modules.customer_care.cc_inbox_jobs import (
    auto_resolve_stale_threads_job,
    send_pending_csat_surveys_job,
)
from app.modules.customer_care.asr_jobs import transcribe_pending_calls_job
from app.modules.bulk_upload.jobs import (
    process_bulk_upload_jobs_job,
    process_bulk_upload_one,
)


async def _startup(_ctx: dict[str, Any]) -> None:
    configure_logging()

    # OpenTelemetry — no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set.
    # Worker uses a distinct service.name so traces in Jaeger / Tempo
    # / etc. are filterable by process kind.
    from app.core.tracing import (
        init_tracing,
        instrument_all_libraries,
        instrument_sqlalchemy,
    )
    init_tracing(service_name="hypershop-worker")
    instrument_all_libraries()
    instrument_sqlalchemy(get_engine())

    # Register outbox handlers from every owning module. The dispatcher
    # routes by event type; without these imports it would dead-letter
    # everything since no handler is registered.
    from app.modules.iam import handlers as _iam_handlers  # noqa: F401
    from app.modules.inventory import handlers as _inv_handlers  # noqa: F401
    from app.modules.invoice_dispatch import handlers as _invoice_handlers  # noqa: F401
    from app.modules.push import handlers as _push_handlers  # noqa: F401
    # Bind WhatsApp / SMS / push transports so the dispatcher has the bound
    # adapters when handlers fire on the worker side too (the api process
    # binds these in lifespan; the worker is a separate Python process).
    from app.modules.invoice_dispatch.transport.whatsapp_factory import (
        bind_from_settings as bind_whatsapp,
    )
    from app.modules.iam.transport.sms_factory import (
        bind_from_settings as bind_sms,
    )
    from app.modules.push.transport.push_factory import (
        bind_from_settings as bind_push,
    )
    from app.modules.search.providers import (
        bind_from_settings as bind_search_reranker,
    )
    bind_whatsapp()
    bind_sms()
    bind_push()
    bind_search_reranker()

    # Module 30 — register every report builder so the scheduled-runs
    # cron can resolve a report code → builder. The api process does
    # the same in main.create_app(); the worker runs in a separate
    # Python process so it must register independently.
    from app.modules.reporting.builders import register_all as _register_reports
    _register_reports()

    # Module 32 — bind the rider_wallet outbox handler so the worker
    # process picks up delivery.delivered events and posts the
    # cod_collection ledger row.
    from app.modules.rider_wallet import handlers as _rw_handlers  # noqa: F401
    # Module 33 — bind the supplier-payment approval-decision email
    # handlers so the worker process picks up approval events emitted
    # by the api process and emails the configured approver lists.
    from app.modules.supplier_payments import handlers as _sp_handlers  # noqa: F401
    # M4 disputes — push handlers auto-register at import time (mirror
    # push/handlers.py pattern). Import triggers the 6 event subscriptions.
    from app.modules.disputes import handlers as _disp_handlers  # noqa: F401

    get_logger("hypershop.worker").info("worker_startup")


async def _shutdown(_ctx: dict[str, Any]) -> None:
    await close_engine()
    await close_redis()
    get_logger("hypershop.worker").info("worker_shutdown")


async def outbox_tick(_ctx: dict[str, Any]) -> int:
    """Cron-driven outbox poller. Runs every 2 seconds."""
    return await dispatch_once()


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(str(settings.redis_url))


class WorkerSettings:
    """ARQ worker config — `arq app.worker.WorkerSettings`."""

    redis_settings = _redis_settings()
    on_startup = _startup
    on_shutdown = _shutdown
    queue_name = get_settings().arq_queue_name
    max_jobs = 20
    # FFmpeg transcode of a 30 s video runs ~10–25 s on a 2-core box;
    # 300 s gives headroom for the 720p+480p pass with veryfast preset
    # plus the Bunny upload at the tail.
    job_timeout = 300
    keep_result = 3600
    # Functions invoked via ``arq_pool.enqueue_job(name, ...)`` from
    # the API process. The string name MUST match the function's
    # __name__ (ARQ uses that for routing).
    functions: ClassVar[list[Any]] = [
        process_product_video,
        process_bulk_upload_one,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        cron(
            outbox_tick,
            second=set(range(0, 60, 2)),
            run_at_startup=True,
            unique=True,
        ),
        cron(
            expire_overdue_batches_job,
            minute=5,  # 5 minutes past every hour
            run_at_startup=True,
            unique=True,
        ),
        cron(
            near_expiry_scan_job,
            hour=2,
            minute=0,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Nightly search-index rebuild at 03:00 UTC = 09:00 BDT
            # (off-peak; the wipe-and-rebuild holds a transaction the
            # whole time so doing it at peak would slow customers).
            search_index_rebuild_job,
            hour=3,
            minute=0,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Reporting platform — pick up scheduled runs every 5 min.
            # Each tick processes up to 50 due schedules; backlog
            # drains FIFO across subsequent ticks.
            run_due_schedules_job,
            minute=set(range(0, 60, 5)),
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Reporting platform — clean up expired report files
            # hourly. Cheap when there's nothing to do; bounded
            # batch size keeps a backlog from blocking the worker.
            cleanup_expired_files_job,
            minute=15,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Rider routing — flip 'available' / 'busy' riders to
            # 'offline' if no ping in last 5 min. Cheap query;
            # exists so the live-map doesn't show stale dots.
            mark_stale_riders_offline_job,
            minute=set(range(0, 60, 2)),  # every 2 min
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Rider routing — capture per-stop ETA snapshots into
            # route_eta_snapshots so SLA-drift dashboards have
            # time-series data to graph.
            snapshot_etas_job,
            minute=set(range(0, 60, 5)),  # every 5 min
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Rider routing — prune rider_live_locations older than
            # 30 days nightly at 02:30 UTC. The location stream is
            # dense (~1.4M rows/day at 1000 riders); without this
            # the table grows unbounded.
            prune_old_live_locations_job,
            hour=2,
            minute=30,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Rider wallet — nightly sweep at 23:30 UTC = 05:30 BDT
            # (just before the new BD operating day) to lock every
            # rider with unpaid balance + no active carry-forward.
            apply_previous_day_settlement_lock_job,
            hour=23,
            minute=30,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Rider wallet — clear expired carry-forward approvals
            # every 30 min. Re-locks the wallet if balance still > 0.
            expire_carry_forwards_job,
            minute={0, 30},
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Supplier payments — refresh recommendation scores
            # nightly at 22:00 UTC = 04:00 BDT next morning so the
            # AP queue is up-to-date when finance opens for the day.
            recompute_all_recommendations_job,
            hour=22,
            minute=0,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Product video FFmpeg processor — pulls 'uploaded' rows
            # every 30s and runs probe + poster + HLS transcode. Tick
            # uses SELECT ... FOR UPDATE SKIP LOCKED so multiple
            # workers can run in parallel without stomping on the
            # same row. Each tick processes at most
            # settings.product_video_max_concurrent_jobs videos so
            # the worker isn't starved by ffmpeg.
            process_pending_videos_job,
            second={0, 30},
            run_at_startup=True,
            unique=True,
        ),
        cron(
            # Product video raw retention — daily at 02:15 UTC
            # (= 08:15 BDT, off-peak). Deletes raw originals on R2
            # (or disk) for terminal-state rows older than
            # settings.product_video_raw_retention_days. The HLS
            # bundle on Bunny is unaffected.
            cleanup_raw_originals_job,
            hour=2,
            minute=15,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Analytical materialized views — refresh hourly at :45
            # so the snapshot is ready before each hour's reporting
            # poll. Missing-view errors are tolerated (job logs +
            # continues) so a fresh install without the analytics
            # migration doesn't trip the cron. See
            # app/modules/reporting/analytics_jobs.py.
            refresh_analytical_materialized_views_job,
            minute=45,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Sponsored Products — roll today_spent_minor to 0 and
            # resume budget-exhausted campaigns at 18:00 UTC = 00:00
            # BDT (start of the new Bangladesh trading day). Without
            # this, no ad serves after a campaign first hits its
            # daily cap.
            reset_daily_budgets_job,
            hour=18,
            minute=0,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Sponsored Products — weekly quality_score recompute on
            # Mondays at 03:00 UTC. 7-day CTR drives the [0.5, 1.5]
            # score that scales each ad's auction rank, so high-CTR
            # ads beat higher bids with sub-baseline quality.
            recompute_quality_scores_job,
            weekday=0,  # Monday
            hour=3,
            minute=0,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Cart recovery (Phase M3.A) — every 30 min, scan open
            # carts past each milestone (1h / 6h / 24h idle) and
            # dispatch a tiered reminder. Bounded per-tick batch keeps
            # a backlog from starving the worker.
            scan_abandoned_carts_job,
            minute={0, 30},
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Cart recovery (Phase M3.A) — daily at 04:00 UTC. Win-back
            # candidates: 7-day dormant + 30-day dormant. Coupon codes
            # WB100 / COMEBACK300 are referenced in the template body;
            # provisioning the codes themselves is a coupons-module
            # task tracked separately.
            scan_dormant_customers_job,
            hour=4,
            minute=0,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Disputes (Phase M4) — hourly at :15, escalate any
            # awaiting_seller dispute past the 48h SLA to under_review
            # so a mediator can pick it up.
            auto_escalate_overdue_disputes_job,
            minute={15},
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # Seller rating (Phase M5 — 2026-05-18). Daily at 02:00 UTC,
            # recompute quality scores for every approved seller from
            # the rolling 30-day metric window.
            recompute_all_seller_ratings_job,
            hour={2},
            minute={0},
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # CC unified inbox (migration 0073 — 2026-05-18). Dispatch
            # pending CSAT surveys every 15 min. Soft-fails to log_only
            # if WhatsApp creds aren't bound.
            send_pending_csat_surveys_job,
            minute={0, 15, 30, 45},
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # CC unified inbox (migration 0073 — 2026-05-18). Hourly at
            # :20, auto-resolve threads stuck in awaiting_customer > 7d
            # so the SLA dashboard isn't polluted with abandoned chats.
            auto_resolve_stale_threads_job,
            minute={20},
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # CC ASR worker — transcribe completed calls with recordings.
            # Every 5 min, bounded batch of 20. Auto-detects provider
            # (Whisper/OpenAI → Whisper local → Google Speech → log_only).
            transcribe_pending_calls_job,
            minute=set(range(0, 60, 5)),
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # M6 customer segmentation (2026-05-18). Recompute RFM scores
            # for every customer with an order in the last 365 days. Daily
            # at 03:00 UTC = 09:00 BDT (off-peak; pre-business-day).
            recompute_all_rfm_scores_job,
            hour=3,
            minute=0,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # M6 customer segmentation (2026-05-18). After RFM recompute,
            # materialize every active segment's membership table. Daily
            # at 03:30 UTC = 09:30 BDT — gives RFM job 30 min to finish.
            materialize_all_segments_job,
            hour=3,
            minute=30,
            run_at_startup=False,
            unique=True,
        ),
        cron(
            # M7 bulk product upload (2026-05-18). Every 30s, pick up to
            # 3 queued jobs and run the CSV/XLSX ingest pipeline.
            # Concurrency kept low — each job batches catalog INSERTs.
            process_bulk_upload_jobs_job,
            second={0, 30},
            run_at_startup=False,
            unique=True,
        ),
    ] + _cc_cron_jobs()


def _cc_cron_jobs() -> list[Any]:
    """Customer-care cron jobs (Module 47). Wrapped in a try/except so
    a missing CC module doesn't crash the worker boot — important
    during partial-rollback scenarios.
    """
    jobs: list[Any] = []
    try:
        from app.modules.customer_care.cron import cc_cron_jobs
        jobs.extend(cc_cron_jobs())
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.modules.marketing.cron import marketing_cron_jobs
        jobs.extend(marketing_cron_jobs())
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.modules.subscriptions.cron import subscriptions_cron_jobs
        jobs.extend(subscriptions_cron_jobs())
    except Exception:  # noqa: BLE001
        pass
    # SEO Domination — 5 cron jobs wired 2026-05-28
    try:
        from app.modules.seo_domination.cron import seo_domination_cron_jobs
        jobs.extend(seo_domination_cron_jobs())
    except Exception:  # noqa: BLE001
        pass
    return jobs
