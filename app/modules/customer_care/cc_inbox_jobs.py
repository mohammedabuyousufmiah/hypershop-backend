"""ARQ jobs for cc_inbox — CSAT dispatch + stale-thread sweep."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.customer_care import cc_inbox_repository as repo
from app.modules.customer_care import cc_inbox_service as svc
from app.modules.customer_care.cc_inbox_models import CCThread, CSATSurvey

_logger = get_logger("hypershop.cc_inbox.jobs")


async def send_pending_csat_surveys_job(_ctx: dict[str, Any]) -> int:
    """Every 15 min — dispatch the next batch of pending CSATs."""
    sent = 0
    async with UnitOfWork().transactional() as session:
        stmt = (
            select(CSATSurvey)
            .where(CSATSurvey.status == "pending", CSATSurvey.sent_at.is_(None))
            .order_by(CSATSurvey.created_at.asc())
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        for r in rows:
            try:
                await svc.send_csat(session, survey_id=r.id)
                sent += 1
            except Exception as e:  # noqa: BLE001
                _logger.warning(
                    "cc_csat_dispatch_failed",
                    survey_id=str(r.id), err=type(e).__name__,
                )
    _logger.info("cc_csat_dispatch_tick", sent=sent)
    return sent


async def auto_resolve_stale_threads_job(_ctx: dict[str, Any]) -> int:
    """Every hour — resolve threads stuck in awaiting_customer > 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    resolved = 0
    async with UnitOfWork().transactional() as session:
        stmt = (
            select(CCThread)
            .where(
                CCThread.status == "awaiting_customer",
                CCThread.last_message_at < cutoff,
            )
            .order_by(CCThread.last_message_at.asc())
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        for r in rows:
            try:
                await svc.resolve_thread(
                    session,
                    thread_id=r.id,
                    by_user_id=r.assigned_agent_id or r.id,
                )
                resolved += 1
            except Exception as e:  # noqa: BLE001
                _logger.warning(
                    "cc_inbox_auto_resolve_failed",
                    thread_id=str(r.id), err=type(e).__name__,
                )
    _logger.info("cc_inbox_auto_resolve_tick", resolved=resolved)
    return resolved


__all__ = [
    "send_pending_csat_surveys_job",
    "auto_resolve_stale_threads_job",
]
