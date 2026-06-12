"""Analytical materialized-view refresh jobs.

Offloads heavy aggregation queries from the hot request path by
periodically refreshing pre-computed snapshots. Reports + executive
dashboards then read from the snapshot instead of running the rollup
on every page load.

Wired into ``app.worker.WorkerSettings.cron_jobs`` — currently scheduled
hourly. Bump to nightly if catalog growth pushes a refresh past 30s
sustained — ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` doesn't take an
exclusive lock but still blocks the writer's concurrent commits when
the row count crosses ~10M.

Adaptation notes (from the Celery snippet this replaces):
    * Hypershop uses ARQ, not Celery (see memory rule). Job signature
      is the ARQ one: ``async def name(ctx: dict[str, Any]) -> int``.
    * No sync ``SessionLocal`` — async session via UnitOfWork.
    * Each REFRESH wrapped in its own try block so one stale/missing
      view doesn't abort the others. Missing views are logged at
      INFO not WARNING — they may not have been created yet on a
      fresh install.
    * ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` requires a unique
      index on the MV. The CREATE MATERIALIZED VIEW migration must
      include one — see ``alembic/versions/*_create_mv_*.py``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text as _text
from sqlalchemy.exc import ProgrammingError

from app.core.db.session import get_engine
from app.core.logging import get_logger

_log = get_logger("hypershop.reporting.analytics_jobs")

# Order matters when MVs depend on each other (refresh upstream first).
# Today these are independent; declared as a tuple for stable iteration.
ANALYTICAL_MATERIALIZED_VIEWS: tuple[str, ...] = (
    "mv_ceo_order_summary",
    "mv_finance_ledger",
    "mv_seller_performance_matrix",
)


async def _refresh_one(mv_name: str) -> bool:
    """Refresh a single MV. Returns True on success, False on either
    'missing' or 'transient error' — caller logs the distinction."""
    engine = get_engine()
    try:
        # Each MV refresh runs in its own autocommit-style connection
        # because REFRESH MATERIALIZED VIEW CONCURRENTLY cannot run
        # inside a transaction block.
        async with engine.connect() as conn:
            await conn.execute(
                _text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv_name}"),
            )
            await conn.commit()
        _log.info("mv_refreshed", view=mv_name)
        return True
    except ProgrammingError as e:
        # MV doesn't exist yet (UndefinedTable) — common on fresh installs
        # before the analytics migration has been applied. Log at INFO
        # and move on rather than treating as a real failure.
        _log.info("mv_skipped_missing", view=mv_name, error=str(e)[:120])
        return False
    except Exception as e:  # noqa: BLE001 — log + continue
        _log.warning("mv_refresh_failed", view=mv_name, error=str(e)[:200])
        return False


async def refresh_analytical_materialized_views_job(
    _ctx: dict[str, Any],
) -> int:
    """ARQ entry point — refreshes every MV in ``ANALYTICAL_MATERIALIZED_VIEWS``.

    Returns the count of MVs successfully refreshed (so ARQ logs +
    metrics surface zero-refresh runs as visible drops).
    """
    refreshed = 0
    for mv in ANALYTICAL_MATERIALIZED_VIEWS:
        if await _refresh_one(mv):
            refreshed += 1
    _log.info(
        "analytical_mv_refresh_complete",
        total=len(ANALYTICAL_MATERIALIZED_VIEWS),
        refreshed=refreshed,
    )
    return refreshed


__all__ = [
    "ANALYTICAL_MATERIALIZED_VIEWS",
    "refresh_analytical_materialized_views_job",
]
