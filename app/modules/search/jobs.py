"""Nightly search-index rebuild ARQ cron job.

Wired into ``app/worker.py`` ``WorkerSettings.cron_jobs``. Runs at
03:00 UTC (= 09:00 BDT). Full rebuild because:
  - Catalog churn is low (a few hundred edits per day at most)
  - Wipe-and-rebuild is simpler than incremental + always converges
    even if the catalog drifts in unexpected ways

If catalog churn grows past ~1k edits/day, switch to incremental:
add an ``EVT_PRODUCT_*`` event in catalog/service.py and have
search.handlers subscribe (out of scope for Module 28 v1).
"""

from __future__ import annotations

from typing import Any

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.core.security.principal import SystemPrincipal

_logger = get_logger("hypershop.search.jobs")


async def search_index_rebuild_job(_ctx: dict[str, Any]) -> int:
    """ARQ entry point — rebuilds the entire search index in one
    transaction. Returns total documents indexed for log/observability.
    """
    from app.modules.search.service import SearchService

    uow = UnitOfWork()
    async with uow.transactional() as session:
        svc = SearchService(session)
        counts = await svc.rebuild_full_index(principal=SystemPrincipal())
    total = sum(counts.values())
    _logger.info("search_index_rebuild_done", **counts, total=total)
    return total
