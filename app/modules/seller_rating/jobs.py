"""ARQ cron jobs for seller rating recompute."""
from __future__ import annotations

from typing import Any

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.seller_rating import repository as repo
from app.modules.seller_rating import service
from app.modules.seller_rating.codes import MAX_SELLERS_PER_TICK

_log = get_logger("hypershop.seller_rating.jobs")


async def recompute_all_seller_ratings_job(
    _ctx: dict[str, Any],
) -> dict[str, int]:
    computed = 0
    failed = 0
    async with UnitOfWork().transactional() as session:
        seller_ids = await repo.list_all_active_seller_ids(
            session, limit=MAX_SELLERS_PER_TICK,
        )
    for sid in seller_ids:
        try:
            async with UnitOfWork().transactional() as session:
                await service.compute_rating_for_seller(session, sid)
            computed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            _log.warning(
                "seller_rating_recompute_failed seller_id=%s err=%s", sid, exc,
            )
    counts = {"computed": computed, "failed": failed, "scanned": len(seller_ids)}
    _log.info("seller_rating_recompute_done", **counts)
    return counts
