"""ARQ cron jobs for customer_segments — daily RFM recompute + materialize."""
from __future__ import annotations

from typing import Any

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.customer_segments import repository as repo
from app.modules.customer_segments import service
from app.modules.customer_segments.codes import LOOKBACK_DAYS

_log = get_logger("hypershop.customer_segments.jobs")

_RECOMPUTE_BATCH = 1000


async def recompute_all_rfm_scores_job(_ctx: dict[str, Any]) -> dict[str, int]:
    """Daily 03:00 UTC — iterate active customers, compute + upsert RFM."""
    scanned = 0
    scored = 0
    offset = 0
    async with UnitOfWork().transactional() as session:
        while True:
            ids = await repo.list_active_customer_ids(
                session, LOOKBACK_DAYS,
                limit=_RECOMPUTE_BATCH, offset=offset,
            )
            if not ids:
                break
            for cid in ids:
                scanned += 1
                try:
                    res = await service.compute_rfm_for_customer(session, cid)
                    if res is not None:
                        scored += 1
                except Exception as e:  # noqa: BLE001
                    _log.warning(
                        "rfm_compute_error customer=%s err=%s", cid, e,
                    )
            offset += _RECOMPUTE_BATCH
            # Bounded per-tick to keep one tick under a few minutes.
            if offset >= 50 * _RECOMPUTE_BATCH:
                break
    _log.info("rfm_recompute_done scanned=%d scored=%d", scanned, scored)
    return {"scanned": scanned, "scored": scored}


async def materialize_all_segments_job(_ctx: dict[str, Any]) -> dict[str, int]:
    """Daily 03:30 UTC — recompute membership table for every active segment."""
    refreshed = 0
    total_members = 0
    async with UnitOfWork().transactional() as session:
        segs, _total = await repo.list_segments(
            session, is_active=True, limit=500, offset=0,
        )
        for seg in segs:
            try:
                n = await service.materialize_segment(session, seg.id)
                refreshed += 1
                total_members += n
            except Exception as e:  # noqa: BLE001
                _log.warning(
                    "materialize_error segment=%s err=%s", seg.code, e,
                )
    _log.info(
        "segments_materialize_done refreshed=%d members=%d",
        refreshed, total_members,
    )
    return {"refreshed": refreshed, "members": total_members}
