"""ARQ cron jobs for disputes — auto-escalate overdue seller responses."""
from __future__ import annotations

from typing import Any

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.disputes import service

_log = get_logger("hypershop.disputes.jobs")


async def auto_escalate_overdue_disputes_job(
    _ctx: dict[str, Any],
) -> dict[str, int]:
    """Hourly — escalate any awaiting_seller disputes past SLA to mediator."""
    async with UnitOfWork().transactional() as session:
        result = await service.auto_escalate_overdue(session)
    _log.info("disputes_auto_escalate", **result)
    return result
