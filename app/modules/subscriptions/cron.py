"""Subscriptions ARQ cron — Module 49.

One job: every 10 minutes, find subscriptions with status='active' AND
next_charge_at <= now() (claim via FOR UPDATE SKIP LOCKED), place an
order for each via OrderService, record the cycle, advance next_charge_at.
"""
from __future__ import annotations

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.subscriptions import service as sub_svc

_log = get_logger("hypershop.subscriptions.cron")


async def charge_due_subscriptions(ctx: dict | None = None) -> dict[str, int]:
    """ARQ entry. Returns counts for observability."""
    placed = 0
    failed = 0
    async with UnitOfWork().transactional() as session:
        due = await sub_svc.claim_due_subscriptions(session, batch_limit=25)
        for sub in due:
            ok, order_id, err = await sub_svc.charge_one_subscription(
                session, sub_row=sub,
            )
            await sub_svc.create_cycle(
                session, subscription=sub, order_id=order_id, ok=ok, error=err,
            )
            if ok:
                placed += 1
            else:
                failed += 1
                _log.warning(
                    "subscription_cycle_failed",
                    sub_id=str(sub[0]), error=err,
                )
    if placed or failed:
        _log.info(
            "subscriptions_charged_due",
            placed=placed, failed=failed,
        )
    return {"placed": placed, "failed": failed}


def subscriptions_cron_jobs() -> list:
    try:
        from arq.cron import cron
    except ImportError:
        return []
    return [
        cron(
            charge_due_subscriptions,
            name="subscriptions_charge_due",
            # Every 10 minutes at :04, :14, …
            minute={4, 14, 24, 34, 44, 54},
            run_at_startup=False,
        ),
    ]
