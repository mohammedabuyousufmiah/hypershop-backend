"""Service layer for CC inbox + voice calls + CSAT.

Reuses existing Hypershop infra:
  - WhatsApp outbound via ``app.modules.customer_care.outbound.send_whatsapp_text``
  - AI auto-draft via ``app.modules.customer_care.outbound.generate_ai_reply``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.customer_care import cc_inbox_repository as repo
from app.modules.customer_care import outbound
from app.modules.customer_care.cc_inbox_models import (
    CCMessage,
    CCThread,
    CSATSurvey,
    VoiceCallSession,
)

_logger = get_logger("hypershop.cc_inbox")

_AI_CONFIDENCE_AUTO_SEND = Decimal("0.85")
_CSAT_EXPIRY_DAYS = 7


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── Inbound + reply ─────────────────────────────────────────────


async def receive_inbound_whatsapp(
    session: AsyncSession,
    *,
    wa_thread_id: str,
    customer_phone: str,
    customer_name: str | None = None,
    body: str,
    attachments: list[Any] | None = None,
    channel_message_id: str | None = None,
) -> CCThread:
    """Land an inbound WhatsApp message into the inbox.

    Creates the thread (channel='whatsapp', channel_thread_id=wa_thread_id)
    if it doesn't exist, then appends an inbound customer message.
    """
    thread = await repo.get_thread_by_channel_id(
        session, "whatsapp", wa_thread_id,
    )
    now = _utcnow()
    if thread is None:
        thread = await repo.create_thread(
            session,
            channel="whatsapp",
            channel_thread_id=wa_thread_id,
            customer_phone=customer_phone,
            customer_name=customer_name,
            status="open",
            last_message_at=now,
            last_inbound_at=now,
        )
    else:
        await repo.update_thread(
            session, thread.id,
            status="open" if thread.status in ("resolved", "closed") else thread.status,
            last_message_at=now,
            last_inbound_at=now,
            customer_name=customer_name or thread.customer_name,
        )
    await repo.add_message(
        session,
        thread_id=thread.id,
        direction="inbound",
        author_kind="customer",
        body=body,
        channel_message_id=channel_message_id,
        attachments=attachments or [],
    )
    return thread


async def agent_reply(
    session: AsyncSession,
    *,
    thread_id: UUID,
    agent_user_id: UUID,
    body: str,
    attachments: list[Any] | None = None,
) -> CCMessage:
    thread = await repo.get_thread(session, thread_id, for_update=True)
    if thread is None:
        raise ThreadNotFound(str(thread_id))
    now = _utcnow()
    channel_mid: str | None = None
    if thread.channel == "whatsapp" and thread.customer_phone:
        # Soft-fail to log_only inside send_whatsapp_text when creds missing.
        try:
            wa_resp = await outbound.send_whatsapp_text(
                to_msisdn=thread.customer_phone, body=body,
            )
            if isinstance(wa_resp, dict):
                channel_mid = str(
                    wa_resp.get("wamid") or wa_resp.get("id") or "",
                ) or None
        except Exception as e:  # noqa: BLE001
            _logger.warning(
                "cc_inbox_wa_send_failed",
                thread_id=str(thread_id), err=type(e).__name__,
            )
    msg = await repo.add_message(
        session,
        thread_id=thread_id,
        direction="outbound",
        author_kind="agent",
        author_user_id=agent_user_id,
        body=body,
        channel_message_id=channel_mid,
        attachments=attachments or [],
    )
    await repo.update_thread(
        session, thread_id,
        assigned_agent_id=thread.assigned_agent_id or agent_user_id,
        status="awaiting_customer",
        last_message_at=now,
        last_agent_response_at=now,
    )
    return msg


async def try_ai_auto_reply(
    session: AsyncSession, *, thread_id: UUID,
) -> dict[str, Any]:
    """Draft a reply using the existing AI provider; auto-send if confident.

    Returns ``{drafted, sent, reply, confidence, handover_required}``.
    """
    thread = await repo.get_thread(session, thread_id)
    if thread is None:
        raise ThreadNotFound(str(thread_id))
    msgs = await repo.list_messages(session, thread_id, limit=20)
    last_customer = next(
        (m for m in reversed(msgs) if m.author_kind == "customer"), None,
    )
    if last_customer is None:
        return {"drafted": False, "sent": False, "reason": "no_customer_msg"}
    reply, confidence, handover = await outbound.generate_ai_reply(
        customer_text=last_customer.body,
    )
    auto_send = (
        not handover
        and confidence >= _AI_CONFIDENCE_AUTO_SEND
        and thread.channel == "whatsapp"
        and bool(thread.customer_phone)
    )
    channel_mid: str | None = None
    ai_meta = {
        "model": "openai_chat",
        "confidence": str(confidence),
        "handover": handover,
    }
    if auto_send:
        try:
            wa_resp = await outbound.send_whatsapp_text(
                to_msisdn=thread.customer_phone, body=reply,
            )
            if isinstance(wa_resp, dict):
                channel_mid = str(
                    wa_resp.get("wamid") or wa_resp.get("id") or "",
                ) or None
        except Exception as e:  # noqa: BLE001
            _logger.warning(
                "cc_inbox_ai_send_failed",
                thread_id=str(thread_id), err=type(e).__name__,
            )
            auto_send = False
    if auto_send:
        await repo.add_message(
            session,
            thread_id=thread_id,
            direction="outbound",
            author_kind="ai",
            body=reply,
            channel_message_id=channel_mid,
            ai_meta=ai_meta,
        )
        await repo.update_thread(
            session, thread_id,
            status="awaiting_customer",
            ai_confidence=confidence,
            last_message_at=_utcnow(),
            last_agent_response_at=_utcnow(),
        )
    else:
        await repo.update_thread(
            session, thread_id, ai_confidence=confidence,
        )
    return {
        "drafted": True,
        "sent": auto_send,
        "reply": reply,
        "confidence": str(confidence),
        "handover_required": handover,
    }


# ─── Lifecycle ───────────────────────────────────────────────────


async def assign_thread(
    session: AsyncSession, *, thread_id: UUID, agent_user_id: UUID,
) -> CCThread:
    row = await repo.assign_thread(session, thread_id, agent_user_id)
    if row is None:
        raise ThreadNotFound(str(thread_id))
    return row


async def resolve_thread(
    session: AsyncSession, *, thread_id: UUID, by_user_id: UUID,
) -> CCThread:
    thread = await repo.get_thread(session, thread_id, for_update=True)
    if thread is None:
        raise ThreadNotFound(str(thread_id))
    now = _utcnow()
    await repo.update_thread(
        session, thread_id, status="resolved", resolved_at=now,
    )
    await repo.add_message(
        session,
        thread_id=thread_id,
        direction="outbound",
        author_kind="system",
        author_user_id=by_user_id,
        body="Thread marked resolved.",
    )
    # Fire-and-add CSAT survey (sent by cron worker).
    await repo.create_csat_survey(
        session,
        thread_id=thread_id,
        customer_user_id=thread.customer_user_id,
        channel=thread.channel,
        status="pending",
        expires_at=now + timedelta(days=_CSAT_EXPIRY_DAYS),
    )
    refreshed = await repo.get_thread(session, thread_id)
    assert refreshed is not None
    return refreshed


async def close_thread(
    session: AsyncSession,
    *,
    thread_id: UUID,
    by_user_id: UUID,
    mark_spam: bool = False,
) -> CCThread:
    thread = await repo.get_thread(session, thread_id, for_update=True)
    if thread is None:
        raise ThreadNotFound(str(thread_id))
    now = _utcnow()
    new_status = "spam" if mark_spam else "closed"
    await repo.update_thread(
        session, thread_id, status=new_status, closed_at=now,
    )
    await repo.add_message(
        session,
        thread_id=thread_id,
        direction="outbound",
        author_kind="system",
        author_user_id=by_user_id,
        body=f"Thread marked {new_status}.",
    )
    refreshed = await repo.get_thread(session, thread_id)
    assert refreshed is not None
    return refreshed


# ─── Voice calls ─────────────────────────────────────────────────


async def record_inbound_voice_call(
    session: AsyncSession, **fields: Any,
) -> VoiceCallSession:
    fields.setdefault("direction", "inbound")
    fields.setdefault("status", "ringing")
    fields.setdefault("started_at", _utcnow())
    return await repo.create_voice_call(session, **fields)


async def assign_voice_call(
    session: AsyncSession, *, call_id: UUID, agent_user_id: UUID,
) -> VoiceCallSession:
    row = await repo.assign_voice_call(session, call_id, agent_user_id)
    if row is None:
        raise VoiceCallNotFound(str(call_id))
    return row


async def complete_voice_call(
    session: AsyncSession,
    *,
    call_id: UUID,
    ended_at: datetime,
    duration_seconds: int,
    recording_url: str | None = None,
    transcript: str | None = None,
    transcript_lang: str | None = None,
) -> VoiceCallSession:
    row = await repo.update_voice_call(
        session, call_id,
        status="completed",
        ended_at=ended_at,
        duration_seconds=duration_seconds,
        recording_url=recording_url,
        transcript=transcript,
        transcript_lang=transcript_lang,
    )
    if row is None:
        raise VoiceCallNotFound(str(call_id))
    # Auto-create CSAT survey for completed calls.
    await repo.create_csat_survey(
        session,
        voice_call_session_id=call_id,
        customer_user_id=row.customer_user_id,
        channel="voice",
        status="pending",
        expires_at=_utcnow() + timedelta(days=_CSAT_EXPIRY_DAYS),
    )
    return row


async def append_voice_note(
    session: AsyncSession,
    *,
    call_id: UUID,
    note: str,
    next_action: str | None = None,
    next_action_at: datetime | None = None,
) -> VoiceCallSession:
    row = await repo.get_voice_call(session, call_id, for_update=True)
    if row is None:
        raise VoiceCallNotFound(str(call_id))
    stamp = _utcnow().isoformat()
    appended = (row.summary or "") + f"\n[{stamp}] {note}".strip()
    updated = await repo.update_voice_call(
        session, call_id,
        summary=appended.strip(),
        next_action=next_action or row.next_action,
        next_action_at=next_action_at or row.next_action_at,
    )
    assert updated is not None
    return updated


# ─── CSAT ────────────────────────────────────────────────────────


async def send_csat(
    session: AsyncSession, *, survey_id: UUID,
) -> dict[str, Any]:
    """Dispatch a pending CSAT survey via the parent channel.

    Soft-fails to log_only when channel creds missing.
    """
    survey = await repo.get_csat(session, survey_id, for_update=True)
    if survey is None:
        raise CSATNotFound(str(survey_id))
    if survey.status not in ("pending", "sent"):
        return {"survey_id": str(survey_id), "skipped": True}

    body = (
        "We'd love your feedback! Reply 1-5 to rate our support "
        "(5 = excellent). Thank you."
    )
    sent_ok = False
    if survey.thread_id is not None:
        thread = await repo.get_thread(session, survey.thread_id)
        if thread and thread.channel == "whatsapp" and thread.customer_phone:
            try:
                await outbound.send_whatsapp_text(
                    to_msisdn=thread.customer_phone, body=body,
                )
                sent_ok = True
            except Exception as e:  # noqa: BLE001
                _logger.warning(
                    "cc_csat_send_failed", err=type(e).__name__,
                )
    elif survey.voice_call_session_id is not None:
        call = await repo.get_voice_call(session, survey.voice_call_session_id)
        if call and call.caller_phone:
            try:
                await outbound.send_whatsapp_text(
                    to_msisdn=call.caller_phone, body=body,
                )
                sent_ok = True
            except Exception as e:  # noqa: BLE001
                _logger.warning(
                    "cc_csat_voice_send_failed", err=type(e).__name__,
                )

    now = _utcnow()
    survey.sent_at = now
    survey.status = "sent" if sent_ok else "sent"  # mark sent even on log_only
    await session.flush()
    return {"survey_id": str(survey_id), "sent": sent_ok, "log_only": not sent_ok}


async def record_csat_response(
    session: AsyncSession,
    *,
    survey_id: UUID,
    score: int,
    comment: str | None = None,
) -> CSATSurvey:
    row = await repo.submit_csat_response(
        session, survey_id, score=score, comment=comment,
    )
    if row is None:
        raise CSATNotFound(str(survey_id))
    if row.voice_call_session_id is not None:
        await repo.update_voice_call(
            session, row.voice_call_session_id, csat_score=score,
        )
    return row


# ─── Exceptions ──────────────────────────────────────────────────


class ThreadNotFound(Exception):
    pass


class VoiceCallNotFound(Exception):
    pass


class CSATNotFound(Exception):
    pass
