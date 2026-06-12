"""Cron jobs for inventory.

Wired into ``app.worker.WorkerSettings.cron_jobs``.

The expire-overdue job runs hourly: it transitions any batch whose
``expiry_date`` has passed into ``status=expired`` and moves its
``available`` + ``reserved`` stock into the ``expired`` bucket.

The near-expiry scan runs daily: it emits one ``inventory.batch.near_expiry``
outbox event per active batch whose ``expiry_date`` falls inside
``settings.inventory_near_expiry_days`` and which still has stock to alert
about. Concrete delivery (email/SMS to the manager/pharmacist on call)
lives in the notifications module that registers a handler for the event
type.
"""

from __future__ import annotations

from typing import Any

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.core.security.principal import SystemPrincipal
from app.modules.inventory.service import InventoryService

_logger = get_logger("hypershop.inventory.jobs")


async def expire_overdue_batches_job(_ctx: dict[str, Any]) -> int:
    """ARQ entry point — runs every hour."""
    uow = UnitOfWork()
    async with uow.transactional() as session:
        svc = InventoryService(session)
        moved = await svc.expire_overdue_batches(principal=SystemPrincipal())
    if moved:
        _logger.info("inventory_expire_overdue", batches_expired=moved)
    return moved


async def near_expiry_scan_job(_ctx: dict[str, Any]) -> int:
    """ARQ entry point — runs daily at 02:00 UTC."""
    uow = UnitOfWork()
    async with uow.transactional() as session:
        svc = InventoryService(session)
        emitted = await svc.scan_near_expiry()
    if emitted:
        _logger.info("inventory_near_expiry_scan", alerts_emitted=emitted)
    return emitted
