"""RQ worker entrypoint. Drains all named queues registered in app.services.QUEUE_NAMES.

Each job payload may carry a `tenant_id` field. Workers honour it via
`with_tenant_bypass()` for global maintenance jobs (sla-scan), or
`with_tenant(payload["tenant_id"])` for per-tenant work, so the row-level
tenant filter still applies when the worker runs queries.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from app.db import SessionLocal
from app.integrations import sheets_client, whatsapp_client
from app.models import AuditLog, CSATSurvey, DEFAULT_TENANT_ID
from app.sla import scan_breaches
from app.tenancy import with_tenant, with_tenant_bypass

logger = logging.getLogger(__name__)


def process_job(queue_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if queue_name == "whatsapp-send-queue":
        if payload.get("type") == "image":
            return asyncio.run(
                whatsapp_client().send_image(
                    payload["to"], payload["image_url"], payload.get("caption")
                )
            )
        if payload.get("to") and payload.get("text"):
            return asyncio.run(whatsapp_client().send_text(payload["to"], payload["text"]))
        return {"processed": True, "queue": queue_name, "skipped": True}

    if queue_name == "google-sheet-sync-queue":
        return sheets_client().append_row(
            payload.get("spreadsheet_id"),
            payload.get("row", []),
            payload.get("tab", "Sheet1"),
        )

    if queue_name == "audit-log-queue":
        db = SessionLocal()
        try:
            db.add(
                AuditLog(
                    tenant_id=payload.get("tenant_id", DEFAULT_TENANT_ID),
                    method=payload.get("method", "?")[:20],
                    path=payload.get("path", "?")[:255],
                    status_code=int(payload.get("status_code", 0)),
                    request_id=(payload.get("request_id") or "-")[:64],
                    ip=(payload.get("ip") or "-")[:64],
                    user_id=payload.get("user_id"),
                )
            )
            db.commit()
            return {"processed": True, "queue": queue_name}
        except Exception:
            logger.exception("audit_worker_failed")
            db.rollback()
            return {"processed": False, "queue": queue_name}
        finally:
            db.close()

    if queue_name == "csat-send-queue":
        from app.csat import start_csat

        db = SessionLocal()
        # CSAT scopes to a single conversation — bypass to read it,
        # then `start_csat` reads convo.tenant_id and writes the survey
        # row with the same tenant_id.
        try:
            with with_tenant_bypass():
                survey = start_csat(db, payload["conversation_id"])
            return {"processed": True, "queue": queue_name, "survey_id": survey.id}
        except Exception:
            logger.exception("csat_worker_failed")
            db.rollback()
            return {"processed": False, "queue": queue_name}
        finally:
            db.close()

    if queue_name == "sla-scan-queue":
        # SLA scan is a cross-tenant maintenance sweep — bypass the tenant filter.
        db = SessionLocal()
        try:
            with with_tenant_bypass():
                counts = scan_breaches(db)
            return {"processed": True, "queue": queue_name, "counts": counts, "ts": datetime.utcnow().isoformat()}
        except Exception:
            logger.exception("sla_worker_failed")
            db.rollback()
            return {"processed": False, "queue": queue_name}
        finally:
            db.close()

    if queue_name == "voice-stt-queue":
        return asyncio.run(_handle_voice_stt(payload))

    if queue_name == "voice-tts-queue":
        return asyncio.run(_handle_voice_tts(payload))

    return {"processed": True, "queue": queue_name, "payload": payload}


async def _handle_voice_stt(payload: dict[str, Any]) -> dict[str, Any]:
    """Download audio from WhatsApp → transcribe via STT → re-enter text pipeline."""
    from app.services import enqueue, receive_whatsapp_text
    from app.voice_note import transcribe

    media_id = payload.get("media_id")
    if not media_id:
        return {"processed": False, "reason": "no_media_id"}

    # Bind tenant context for the duration of this job so all DB queries
    # in the re-entered pipeline are scoped correctly.
    job_tenant = payload.get("tenant_id") or "default"

    try:
        audio, mime = await whatsapp_client().download_media(media_id)
    except Exception as exc:
        logger.exception("voice_stt_download_failed media_id=%s", media_id)
        _dlq_voice("voice-stt-download", payload, exc)
        return {"processed": False, "reason": "download_failed"}

    if not audio:
        return {"processed": False, "reason": "empty_audio"}

    try:
        result = await transcribe(audio, mime=mime or payload.get("media_mime") or "audio/ogg")
    except Exception as exc:
        logger.exception("voice_stt_transcribe_failed media_id=%s", media_id)
        _dlq_voice("voice-stt-transcribe", payload, exc)
        return {"processed": False, "reason": "transcribe_failed"}

    transcript = (result.text or "").strip()
    if not transcript:
        logger.warning("voice_stt_empty_transcript media_id=%s provider=%s", media_id, result.provider)
        return {"processed": True, "queue": "voice-stt-queue", "transcript": "", "provider": result.provider}

    # Re-enter the same pipeline as a regular text message — preserves dedup,
    # AI reply, SLA, agent assignment, etc. Tenant context is set so the
    # row-level filter scopes all queries to this job's tenant.
    db = SessionLocal()
    try:
      with with_tenant(job_tenant):
        convo = receive_whatsapp_text(
            db,
            phone=payload.get("from_phone", ""),
            text=transcript,
            message_id=f"{payload.get('channel_message_id', media_id)}::stt",
        )
        # If the text reply was generated, schedule a TTS reply so the customer
        # hears a voice note back. The receive_whatsapp_text call already added
        # the AI reply Message row to the conversation.
        if convo is not None:
            enqueue(
                "voice-tts-queue",
                {
                    "tenant_id": payload.get("tenant_id"),
                    "to": payload.get("from_phone"),
                    "conversation_id": convo.id,
                    "voice_note_flag": payload.get("is_voice_note", True),
                    "source": "stt_reply",
                },
            )
    finally:
        db.close()

    return {
        "processed": True,
        "queue": "voice-stt-queue",
        "transcript_chars": len(transcript),
        "provider": result.provider,
        "language": result.language,
    }


async def _handle_voice_tts(payload: dict[str, Any]) -> dict[str, Any]:
    """Synthesize a TTS reply for the conversation's last AI message and send
    it back as an audio (or voice-note) WhatsApp message."""
    from sqlalchemy import select
    from app.models import Message
    from app.voice_note import synthesize

    convo_id = payload.get("conversation_id")
    explicit_text = payload.get("text")
    job_tenant = payload.get("tenant_id") or "default"

    text: str | None = explicit_text
    if not text and convo_id:
        db = SessionLocal()
        try:
            with with_tenant(job_tenant):
                last_ai = db.scalar(
                    select(Message)
                    .where(Message.conversation_id == convo_id, Message.sender_type == "ai")
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                if last_ai:
                    text = last_ai.message_body
        finally:
            db.close()

    if not text or not text.strip():
        return {"processed": False, "reason": "no_text_for_tts"}

    to = payload.get("to")
    if not to:
        return {"processed": False, "reason": "no_recipient"}

    try:
        synth = await synthesize(text, language=payload.get("language"))
    except Exception as exc:
        logger.exception("voice_tts_synthesize_failed conv=%s", convo_id)
        _dlq_voice("voice-tts-synth", payload, exc)
        return {"processed": False, "reason": "synthesize_failed"}

    if not synth.audio:
        # Dry-run mode (provider not configured) — log and skip silently.
        logger.warning("voice_tts_skipped_dry_run conv=%s provider=%s", convo_id, synth.provider)
        return {"processed": True, "queue": "voice-tts-queue", "dry_run": True}

    try:
        media_id = await whatsapp_client().upload_media(synth.audio, mime=synth.mime)
        send_result = await whatsapp_client().send_voice(
            to,
            media_id,
            voice_note=bool(payload.get("voice_note_flag")) and synth.voice_flag_supported,
        )
    except Exception as exc:
        logger.exception("voice_tts_send_failed conv=%s", convo_id)
        _dlq_voice("voice-tts-send", payload, exc)
        return {"processed": False, "reason": "send_failed"}

    return {
        "processed": True,
        "queue": "voice-tts-queue",
        "media_id": media_id,
        "provider": synth.provider,
        "send": send_result,
    }


def _dlq_voice(operation: str, payload: dict[str, Any], exc: BaseException) -> None:
    from app.dlq import write_dlq

    db = SessionLocal()
    try:
        write_dlq(
            db,
            source="voice_note",
            operation=operation,
            payload=payload,
            error=exc,
        )
    finally:
        db.close()
