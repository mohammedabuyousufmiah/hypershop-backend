"""ARQ cron jobs for cart_recovery — scan + dispatch."""
from __future__ import annotations

from typing import Any

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.cart_recovery import repository as repo
from app.modules.cart_recovery import service
from app.modules.cart_recovery.codes import (
    CART_MILESTONES,
    WINBACK_MILESTONES,
)

_log = get_logger("hypershop.cart_recovery.jobs")


async def scan_abandoned_carts_job(_ctx: dict[str, Any]) -> dict[str, int]:
    """Every 30 min — pick due carts per milestone, dispatch each."""
    counts: dict[str, int] = {}
    async with UnitOfWork().transactional() as session:
        for m in CART_MILESTONES:
            rows = await repo.list_carts_due_for_milestone(session, m, limit=500)
            for r in rows:
                try:
                    await service.dispatch_for_cart(session, milestone=m, **r)
                except Exception as e:  # noqa: BLE001
                    _log.warning(
                        "cart_recovery_dispatch_error milestone=%s cart=%s err=%s",
                        m, r.get("cart_id"), e,
                    )
            counts[m] = len(rows)
    _log.info("cart_recovery_cart_scan", **counts)
    return counts


async def scan_dormant_customers_job(_ctx: dict[str, Any]) -> dict[str, int]:
    """Daily — pick win-back candidates per milestone, dispatch each."""
    counts: dict[str, int] = {}
    async with UnitOfWork().transactional() as session:
        for m in WINBACK_MILESTONES:
            rows = await repo.list_customers_due_for_winback(session, m, limit=500)
            for r in rows:
                try:
                    await service.dispatch_for_winback(session, milestone=m, **r)
                except Exception as e:  # noqa: BLE001
                    _log.warning(
                        "cart_recovery_winback_error milestone=%s user=%s err=%s",
                        m, r.get("customer_user_id"), e,
                    )
            counts[m] = len(rows)
    _log.info("cart_recovery_winback_scan", **counts)
    return counts
