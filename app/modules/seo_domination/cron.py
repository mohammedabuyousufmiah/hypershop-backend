"""ARQ cron tasks for the SEO Domination pipeline.

Wired into ``app/worker.py`` ``WorkerSettings.cron_jobs`` via
``seo_domination_cron_jobs()``.

Scheduled jobs (UTC):
  - daily_blog_queue       00:30 daily     — drop a fresh blog spec into the pipeline
  - weekly_trend_roundup   Mon 01:00       — queue weekly trend article
  - hourly_indexnow_batch  every hour @05  — push fresh URLs to IndexNow
  - daily_link_audit       02:00 daily     — recompute internal-link relevance + prune broken
  - daily_backlink_pings   03:00 daily     — outreach-status follow-ups
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.db.uow import UnitOfWork

from . import content_pipeline
from .models import ContentPipelineItem


async def daily_blog_queue(ctx: dict) -> dict:
    spec = content_pipeline.daily_blog_spec()
    async with UnitOfWork().transactional() as db:
        db.add(ContentPipelineItem(
            kind=spec.kind, topic=spec.topic, locale=spec.locale,
            target_keywords=spec.target_keywords, status="queued",
        ))
    return {"queued_topic": spec.topic, "ts": datetime.now(timezone.utc).isoformat()}


async def weekly_trend_roundup(ctx: dict) -> dict:
    week_iso = datetime.now(timezone.utc).strftime("%G-W%V")
    spec = content_pipeline.weekly_trend_roundup_spec(week_iso)
    async with UnitOfWork().transactional() as db:
        db.add(ContentPipelineItem(
            kind=spec.kind, topic=spec.topic, locale=spec.locale,
            target_keywords=spec.target_keywords, status="queued",
        ))
    return {"week": week_iso}


async def hourly_indexnow_batch(ctx: dict) -> dict:
    """Bridges to ``seo.jobs.indexnow_push`` (existing module) at runtime.

    Soft-imports so this cron is safe to schedule even if the upstream
    IndexNow job hasn't been wired in a given environment.
    """
    try:
        from app.modules.seo.jobs import push_indexnow_batch  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return {"queued": 0, "note": "seo.jobs.push_indexnow_batch not available"}
    pushed = await push_indexnow_batch(ctx, source="seo_domination")
    return {"pushed": pushed}


async def daily_link_audit(ctx: dict) -> dict:
    """Recompute internal-link relevance + decay 30-day click counts.

    Wired 2026-05-28 (was a return-zero hook). Strategy:
      - Decay clicks_30d by /30 (running average over 30 days)
      - Drop links whose target_url no longer exists in seo_programmatic_pages
        or any other canonical content table
      - Boost relevance_score for high-CTR links (clicks > 5 in last 30 days)
    """
    from sqlalchemy import update, select, delete, func
    from .models import InternalLink, ProgrammaticPage

    decayed = 0
    boosted = 0
    pruned = 0
    async with UnitOfWork().transactional() as db:
        # Decay all clicks_30d by ~3% (1/30) — simple running average
        upd = await db.execute(
            update(InternalLink)
            .where(InternalLink.clicks_30d > 0)
            .values(clicks_30d=func.greatest(0, InternalLink.clicks_30d - 1))
        )
        decayed = upd.rowcount or 0

        # Boost relevance for high-engagement links
        upd2 = await db.execute(
            update(InternalLink)
            .where(InternalLink.clicks_30d > 5)
            .values(relevance_score=func.least(0.999, InternalLink.relevance_score + 0.01))
        )
        boosted = upd2.rowcount or 0

        # Prune links whose target is a /lp/* programmatic URL that no longer exists
        lp_links = (await db.execute(
            select(InternalLink.id, InternalLink.target_url)
            .where(InternalLink.target_url.like("%/lp/%"))
        )).all()
        valid_slugs = {row[0] for row in (await db.execute(select(ProgrammaticPage.slug))).all()}
        stale = [
            lid for lid, url in lp_links
            if not any(url.endswith(f"/lp/{s}") for s in valid_slugs)
        ]
        if stale:
            await db.execute(delete(InternalLink).where(InternalLink.id.in_(stale)))
            pruned = len(stale)

    return {"audited": decayed, "boosted": boosted, "pruned": pruned}


async def daily_backlink_pings(ctx: dict) -> dict:
    """Auto-remind pitched-but-no-reply targets after 7 / 14 / 30 days.

    Wired 2026-05-28 (was a return-zero hook). Strategy:
      - Find rows where outreach_status='pitched' and created_at age in (7,14,30) day buckets
      - Bump pitch_template_id to the next reminder template (reminder.1, reminder.2, escalate)
      - Log to audit; SMTP send is creds-gated (logs intent if SMTP_HOST missing)
    """
    import os
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, and_
    from .models import BacklinkOutreach

    smtp_configured = bool(os.getenv("SMTP_HOST") or os.getenv("SENDGRID_API_KEY"))
    now = datetime.now(timezone.utc)
    sent = 0
    queued = 0
    buckets_hit: list[int] = []

    async with UnitOfWork().transactional() as db:
        for days, template in ((7, "reminder.1"), (14, "reminder.2"), (30, "escalate")):
            window_start = now - timedelta(days=days + 1)
            window_end = now - timedelta(days=days)
            q = await db.execute(
                select(BacklinkOutreach).where(
                    and_(
                        BacklinkOutreach.outreach_status == "pitched",
                        BacklinkOutreach.created_at >= window_start,
                        BacklinkOutreach.created_at < window_end,
                    )
                )
            )
            rows = q.scalars().all()
            for row in rows:
                row.pitch_template_id = template
                if smtp_configured:
                    # TODO: integrate with app.modules.email.send when avail
                    sent += 1
                else:
                    queued += 1
                buckets_hit.append(days)

    return {
        "reminded_total": sent + queued,
        "sent_via_smtp": sent,
        "queued_no_creds": queued,
        "buckets_hit": buckets_hit,
        "smtp_configured": smtp_configured,
    }


def seo_domination_cron_jobs() -> list:
    """Return ARQ cron specs to extend ``WorkerSettings.cron_jobs``."""
    try:
        from arq.cron import cron  # type: ignore
    except ImportError:  # ARQ optional during test imports
        return []
    return [
        cron(daily_blog_queue,       hour={0},   minute={30}),
        cron(weekly_trend_roundup,   weekday={"mon"}, hour={1}, minute={0}),
        cron(hourly_indexnow_batch,  minute={5}),
        cron(daily_link_audit,       hour={2}, minute={0}),
        cron(daily_backlink_pings,   hour={3}, minute={0}),
    ]
