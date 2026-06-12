"""Periodic background work for customer-care.

Two jobs:
- ``scan_sla_breaches`` — every 60s. Marks
  ``sla_first_response_breached`` / ``sla_resolution_breached`` on any
  open conversation past its due time + raises priority + flags
  handover_required.
- ``send_due_followups`` — every 5 min. Picks up ``cc_followups`` rows
  whose ``next_followup_at`` is in the past, sends a templated
  WhatsApp message, advances the stage, schedules the next one.

Wired into Hypershop's ARQ worker via ``register_cc_cron_jobs(...)``
which returns the cron specs to append to ``worker.WorkerSettings.cron_jobs``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, text as _text, update

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.customer_care import outbound
from app.modules.customer_care.models import (
    CCConversation,
    CCFollowup,
    CCSlaPolicy,
)

_log = get_logger("hypershop.customer_care.cron")


# ----------------------------------------------------------------- SLA
DEFAULT_FIRST_RESPONSE_MIN = 15
DEFAULT_RESOLUTION_MIN = 240


async def _ensure_due_dates(session, conv: CCConversation) -> None:
    """Lazy-set SLA due dates on conversations that pre-date this
    cron job. Looks up the policy for the priority, falls back to
    defaults if no row exists.
    """
    if conv.sla_first_response_due_at and conv.sla_resolution_due_at:
        return
    policy = (
        await session.execute(
            select(CCSlaPolicy)
            .where(CCSlaPolicy.is_active.is_(True))
            .where(CCSlaPolicy.priority == conv.priority)
            .limit(1)
        )
    ).scalar_one_or_none()
    if policy is None:
        first_min = DEFAULT_FIRST_RESPONSE_MIN
        resolution_min = DEFAULT_RESOLUTION_MIN
    else:
        first_min = policy.first_response_minutes
        resolution_min = policy.resolution_minutes
    if not conv.sla_first_response_due_at:
        conv.sla_first_response_due_at = conv.created_at + timedelta(minutes=first_min)
    if not conv.sla_resolution_due_at:
        conv.sla_resolution_due_at = conv.created_at + timedelta(minutes=resolution_min)


async def scan_sla_breaches(ctx: dict | None = None) -> dict[str, int]:
    """ARQ cron entry point. Returns counts for observability."""
    now = datetime.now(timezone.utc)
    fr_marked = 0
    res_marked = 0
    async with UnitOfWork().transactional() as session:
        # Open conversations to consider
        convs = (
            await session.execute(
                select(CCConversation).where(CCConversation.status == "open")
            )
        ).scalars().all()
        for c in convs:
            await _ensure_due_dates(session, c)
            # First-response breach
            if (
                not c.sla_first_response_breached
                and c.first_response_at is None
                and c.sla_first_response_due_at
                and c.sla_first_response_due_at < now
            ):
                c.sla_first_response_breached = True
                c.priority = "high"
                c.handover_required = True
                c.handover_reason = c.handover_reason or "sla_first_response_breach"
                fr_marked += 1
            # Resolution breach
            if (
                not c.sla_resolution_breached
                and c.resolved_at is None
                and c.sla_resolution_due_at
                and c.sla_resolution_due_at < now
            ):
                c.sla_resolution_breached = True
                c.priority = "high"
                c.handover_required = True
                c.handover_reason = c.handover_reason or "sla_resolution_breach"
                res_marked += 1
    if fr_marked or res_marked:
        _log.warning(
            "cc_sla_breaches_marked",
            first_response=fr_marked,
            resolution=res_marked,
        )
    return {"first_response_marked": fr_marked, "resolution_marked": res_marked}


# ----------------------------------------------------------------- Followups
async def send_due_followups(ctx: dict | None = None) -> dict[str, int]:
    """ARQ cron entry point. Sends a WhatsApp drip message for every
    ``cc_followups`` row with ``status='pending'`` and
    ``next_followup_at <= now()``. Idempotent on (id, stage).

    Respects ``cc_customer_profile.consent_status='stopped'`` — those
    rows are NOT sent and are marked ``status='consent_stopped'``.
    """
    now = datetime.now(timezone.utc)
    sent = 0
    skipped_no_phone = 0
    skipped_consent = 0
    failed = 0
    async with UnitOfWork().transactional() as session:
        rows = (
            await session.execute(
                select(CCFollowup).where(
                    CCFollowup.status == "pending",
                    (
                        CCFollowup.next_followup_at.is_(None)
                        | (CCFollowup.next_followup_at <= now)
                    ),
                ).limit(50)
            )
        ).scalars().all()
        # Pull all customer phones + consent in one go
        ids = [r.customer_id for r in rows]
        phones: dict[UUID, str] = {}
        consent: dict[UUID, str] = {}
        if ids:
            rs = (
                await session.execute(
                    _text(
                        "SELECT u.id, u.phone, p.consent_status "
                        "FROM users u "
                        "LEFT JOIN cc_customer_profile p ON p.customer_id = u.id "
                        "WHERE u.id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
            ).all()
            phones = {row[0]: row[1] for row in rs if row[1]}
            consent = {row[0]: (row[2] or "allowed") for row in rs}
        # Send each (network IO inside the txn is OK — small batch)
        for f in rows:
            if consent.get(f.customer_id, "allowed") == "stopped":
                f.status = "consent_stopped"
                skipped_consent += 1
                continue
            phone = phones.get(f.customer_id)
            if not phone:
                skipped_no_phone += 1
                f.status = "no_phone"
                continue
            body = (
                f"Hypershop · {f.campaign_name}: "
                f"We thought you'd like to know — your items might still be "
                f"available. Reply HELP to chat with an agent or STOP to opt out."
            )
            result = await outbound.send_whatsapp_text(to_phone=phone, body=body)
            if result is None:
                # Either no creds or HTTP error. Defer instead of fail.
                failed += 1
                f.next_followup_at = now + timedelta(hours=1)
                continue
            f.last_sent_at = now
            f.stage += 1
            f.next_followup_at = (
                now + timedelta(days=1) if f.stage <= 3 else None
            )
            f.status = "pending" if f.stage <= 3 else "done"
            sent += 1
    if sent or failed or skipped_consent:
        _log.info(
            "cc_followups_dispatched",
            sent=sent, failed=failed,
            skipped_no_phone=skipped_no_phone,
            skipped_consent=skipped_consent,
        )
    return {
        "sent": sent, "failed": failed,
        "skipped_no_phone": skipped_no_phone,
        "skipped_consent": skipped_consent,
    }


# ============================================================ embed worker
async def embed_pending_kb_chunks(ctx: dict | None = None) -> dict[str, int]:
    """Find chunks with NULL embedding, call OpenAI in batches of 20,
    update the column. Runs every 5 minutes.

    No-op if ``OPENAI_API_KEY`` is missing (degrades to LIKE search).
    """
    import json as _json
    from app.modules.customer_care.config import settings as _cc_settings
    if not _cc_settings().openai_api_key:
        return {"embedded": 0, "skipped_no_key": 1}
    embedded = 0
    failed = 0
    async with UnitOfWork().transactional() as session:
        rows = (
            await session.execute(
                _text(
                    "SELECT id, text FROM cc_knowledge_chunks "
                    "WHERE embedding IS NULL "
                    "ORDER BY created_at ASC LIMIT 50"
                ),
            )
        ).all()
        if not rows:
            return {"embedded": 0}
        ids = [r[0] for r in rows]
        texts = [r[1] for r in rows]
        from app.modules.customer_care import outbound
        embs = await outbound.embed_texts(texts)
        if not embs:
            failed = len(rows)
        else:
            cfg = _cc_settings()
            for cid, vec in zip(ids, embs):
                if not vec:
                    failed += 1
                    continue
                await session.execute(
                    _text(
                        "UPDATE cc_knowledge_chunks "
                        "SET embedding = :emb, embedding_model = :m, embedding_dim = :d "
                        "WHERE id = :cid"
                    ),
                    {
                        "emb": _json.dumps(vec),
                        "m": cfg.openai_embedding_model,
                        "d": len(vec),
                        "cid": cid,
                    },
                )
                embedded += 1
    if embedded or failed:
        _log.info("cc_kb_embed_batch", embedded=embedded, failed=failed)
    return {"embedded": embedded, "failed": failed}


# ============================================================ outbound retry
async def retry_outbound_dead_letters(ctx: dict | None = None) -> dict[str, int]:
    """Picks up ``cc_dead_letters`` rows where source='whatsapp_send'
    and status='pending', attempts re-send. Caps at 3 retries total
    (counts ``attempts`` column), then marks status='dead'.
    """
    import json as _json
    from app.modules.customer_care import outbound
    now = datetime.now(timezone.utc)
    retried = 0
    succeeded = 0
    permanently_dead = 0
    async with UnitOfWork().transactional() as session:
        rows = (
            await session.execute(
                select(CCDeadLetter).where(
                    CCDeadLetter.source == "whatsapp_send",
                    CCDeadLetter.status == "pending",
                ).limit(20)
            )
        ).scalars().all()
        for r in rows:
            try:
                p = _json.loads(r.payload or "{}")
            except Exception:  # noqa: BLE001
                r.status = "malformed"
                continue
            retried += 1
            result = None
            try:
                if p.get("kind") == "text":
                    result = await outbound.send_whatsapp_text(
                        to_phone=p["to_phone"], body=p["body"],
                    )
                elif p.get("kind") == "image":
                    result = await outbound.send_whatsapp_image(
                        to_phone=p["to_phone"], image_url=p["image_url"],
                        caption=p.get("caption"),
                    )
                elif p.get("kind") == "template":
                    result = await outbound.send_whatsapp_template(
                        to_phone=p["to_phone"],
                        template_name=p["template_name"],
                        body_params=p.get("body_params") or [],
                    )
            except Exception as e:  # noqa: BLE001
                r.error_message = str(e)[:1000]
            r.attempts += 1
            r.last_attempt_at = now
            if result is not None:
                r.status = "succeeded"
                succeeded += 1
            elif r.attempts >= 3:
                r.status = "dead"
                permanently_dead += 1
    if retried:
        _log.info(
            "cc_outbound_retry",
            retried=retried, succeeded=succeeded, permanently_dead=permanently_dead,
        )
    return {
        "retried": retried, "succeeded": succeeded,
        "permanently_dead": permanently_dead,
    }


# Imported here so the retry job can reference the model without
# circular import surprises at module-load time.
from app.modules.customer_care.models import CCDeadLetter  # noqa: E402


# ----------------------------------------------------------------- ARQ wiring
def cc_cron_jobs() -> list:
    """Return ARQ cron-job definitions for the worker to register.

    Two entries — one for SLA scan (every minute) and one for follow-up
    sender (every 5 minutes). The Hypershop worker imports this from
    its module-level cron registration block.
    """
    try:
        from arq.cron import cron
    except ImportError:
        return []
    return [
        cron(
            scan_sla_breaches,
            name="cc_sla_breach_scan",
            second={0},   # every minute, at :00
            run_at_startup=False,
        ),
        cron(
            send_due_followups,
            name="cc_followup_sender",
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
        cron(
            embed_pending_kb_chunks,
            name="cc_kb_embed_worker",
            minute={1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56},
            run_at_startup=False,
        ),
        cron(
            retry_outbound_dead_letters,
            name="cc_outbound_retry",
            minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57},
            run_at_startup=False,
        ),
    ]
