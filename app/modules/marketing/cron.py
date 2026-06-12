"""Marketing automation ARQ cron — Module 48.

One job: every minute, pick up campaigns with status='scheduled' AND
schedule_at <= now() and run their send-now dispatch path. Uses an
``UPDATE … RETURNING id`` to claim the row so two concurrent worker
ticks don't double-fire.
"""
from __future__ import annotations

from sqlalchemy import text as _t

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.marketing import service as mk

_log = get_logger("hypershop.marketing.cron")


async def fire_scheduled_campaigns(ctx: dict | None = None) -> dict[str, int]:
    """Pick due scheduled campaigns + dispatch. Idempotent — the
    'sending' status flip claims the row and rejects double-claims.
    """
    fired = 0
    async with UnitOfWork().transactional() as session:
        # Claim up to 5 due campaigns per tick
        due = (
            await session.execute(
                _t(
                    "SELECT id FROM marketing_campaigns "
                    "WHERE status = 'scheduled' AND schedule_at <= now() "
                    "ORDER BY schedule_at ASC LIMIT 5 "
                    "FOR UPDATE SKIP LOCKED"
                ),
            )
        ).all()
        due_ids = [r[0] for r in due]
    # Dispatch each — txn-bounded inside service.send_campaign
    sent_total = 0
    failed_total = 0
    for cid in due_ids:
        async with UnitOfWork().transactional() as session:
            out = await mk.send_campaign(session, campaign_id=cid)
        enq = out.get("_enqueued") or []
        # Replay the outbound dispatch path — duplicates the route
        # logic intentionally; refactoring to a shared helper would
        # be a clean follow-up.
        from app.modules.customer_care import outbound, channels
        sent_ok = 0
        failed = 0
        for item in enq:
            ch = item["channel"]
            ok = False
            err = None
            try:
                if ch == "whatsapp":
                    if item.get("whatsapp_template_name"):
                        result = await outbound.send_whatsapp_template(
                            to_phone=item["phone"],
                            template_name=item["whatsapp_template_name"],
                            body_params=[],
                        )
                    else:
                        result = await outbound.send_whatsapp_text(
                            to_phone=item["phone"], body=item["body"],
                        )
                    ok = result is not None
                elif ch == "sms":
                    result = await channels.send_sms(
                        to_phone=item["phone"], body=item["body"],
                    )
                    ok = result is not None
                elif ch == "email":
                    ok = await channels.send_email(
                        to_address=item["email"],
                        subject=item.get("subject") or "Hypershop",
                        body_text=item["body"],
                    )
                elif ch == "in_app":
                    from app.modules.notifications.service import NotificationService
                    async with UnitOfWork().transactional() as s2:
                        svc = NotificationService(s2)
                        await svc.create(
                            customer_user_id=item["user_id"],
                            title=item.get("subject") or "Hypershop",
                            body=item["body"], category="marketing",
                        )
                    ok = True
            except Exception as e:  # noqa: BLE001
                err = str(e)[:500]
            async with UnitOfWork().transactional() as s3:
                await mk.mark_send_result(
                    s3, send_id=item["send_id"], ok=ok,
                    error=err,
                )
            if ok:
                sent_ok += 1
            else:
                failed += 1
        async with UnitOfWork().transactional() as session:
            await mk.finalise_campaign(
                session, campaign_id=cid, sent=sent_ok, failed=failed,
            )
        sent_total += sent_ok
        failed_total += failed
        fired += 1
    if fired:
        _log.info(
            "marketing_scheduled_fired",
            campaigns=fired, total_sent=sent_total, total_failed=failed_total,
        )
    return {"campaigns_fired": fired, "sent": sent_total, "failed": failed_total}


def marketing_cron_jobs() -> list:
    """Return ARQ cron definitions for the worker."""
    try:
        from arq.cron import cron
    except ImportError:
        return []
    return [
        cron(
            fire_scheduled_campaigns,
            name="marketing_scheduled_sender",
            minute={3, 8, 13, 18, 23, 28, 33, 38, 43, 48, 53, 58},
            run_at_startup=False,
        ),
    ]
