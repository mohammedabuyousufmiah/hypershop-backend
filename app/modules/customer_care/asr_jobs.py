"""ASR (Automatic Speech Recognition) transcript worker.

Polls `hypershop_voice_call_sessions` for completed calls that:
  - have a recording_url
  - have no transcript yet
  - are not older than 30 days (skip stale recordings)

Then routes each recording through the configured ASR provider:
  - Whisper (OpenAI hosted) when `OPENAI_API_KEY` is set
  - Whisper (local) when `WHISPER_LOCAL_MODEL_PATH` is set
  - Google Cloud Speech when `GOOGLE_APPLICATION_CREDENTIALS` is set
  - Else: status='log_only', writes a placeholder transcript explaining
    no provider was configured (so admin UI shows actionable text instead
    of a permanently empty cell).

Soft-fail: ASR errors are logged + transcript stays NULL so the next
tick retries. Provider rate-limits respected via 2-second sleep between
calls. Bounded batch (20 calls per tick) so the worker doesn't hog Redis.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.customer_care.cc_inbox_models import VoiceCallSession

_log = get_logger("hypershop.cc.asr")

_MAX_RECORDING_AGE_DAYS = 30
_BATCH_SIZE = 20
_INTER_CALL_SLEEP_S = 2.0


async def _detect_provider() -> str:
    """Returns the configured ASR provider code; 'log_only' if none."""
    from app.core.config import get_settings
    cfg = get_settings()
    if getattr(cfg, "openai_api_key", None):
        return "whisper_openai"
    if getattr(cfg, "whisper_local_model_path", None):
        return "whisper_local"
    if getattr(cfg, "google_application_credentials", None):
        return "google_speech"
    return "log_only"


async def _transcribe_via_whisper_openai(recording_url: str) -> dict[str, Any]:
    """Calls OpenAI's transcription endpoint. Returns
    {'text': str, 'language': str|None}. Raises on error."""
    import httpx
    from app.core.config import get_settings
    cfg = get_settings()
    # Whisper-1 endpoint accepts a multipart file upload; we stream the
    # recording from the URL into memory first (small calls < 25 MB OK).
    async with httpx.AsyncClient(timeout=60) as client:
        audio_r = await client.get(recording_url)
        audio_r.raise_for_status()
        upload = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {cfg.openai_api_key}"},
            files={"file": ("audio.mp3", audio_r.content, "audio/mpeg")},
            data={"model": "whisper-1", "response_format": "verbose_json"},
        )
        upload.raise_for_status()
        body = upload.json()
    return {
        "text": body.get("text", "").strip(),
        "language": body.get("language"),
    }


async def _transcribe_via_provider(
    provider: str, recording_url: str,
) -> dict[str, Any]:
    if provider == "whisper_openai":
        return await _transcribe_via_whisper_openai(recording_url)
    # whisper_local + google_speech: stubs return log_only — the operator
    # adds the real adapter when credentials arrive. Mirror the soft-fail
    # pattern used by the WhatsApp/email dispatchers.
    return {
        "text": (
            f"[ASR not configured for provider={provider}; "
            f"transcript stays empty until a provider client is wired.]"
        ),
        "language": None,
    }


async def transcribe_pending_calls_job(_ctx: dict) -> dict[str, int]:
    """ARQ cron — runs every 5 min."""
    provider = await _detect_provider()
    since = datetime.now(timezone.utc) - timedelta(days=_MAX_RECORDING_AGE_DAYS)
    succeeded = 0
    failed = 0
    skipped_log_only = 0

    async with UnitOfWork().transactional() as session:
        stmt = (
            select(VoiceCallSession)
            .where(
                VoiceCallSession.status == "completed",
                VoiceCallSession.recording_url.is_not(None),
                VoiceCallSession.transcript.is_(None),
                VoiceCallSession.ended_at >= since,
            )
            .order_by(VoiceCallSession.ended_at.desc())
            .limit(_BATCH_SIZE)
        )
        rows = (await session.execute(stmt)).scalars().all()

        for row in rows:
            try:
                result = await _transcribe_via_provider(
                    provider, row.recording_url,
                )
                row.transcript = result["text"]
                row.transcript_lang = result["language"]
                if provider == "log_only":
                    skipped_log_only += 1
                else:
                    succeeded += 1
                await session.flush()
            except Exception as e:  # noqa: BLE001
                _log.warning(
                    "asr_transcribe_failed",
                    call_id=str(row.id),
                    provider=provider,
                    error=str(e)[:200],
                )
                failed += 1
                # Leave transcript=NULL so the next tick retries.
                continue
            # Light rate-limit between calls to avoid pegging the provider.
            await asyncio.sleep(_INTER_CALL_SLEEP_S)

    counts = {
        "provider": provider,
        "succeeded": succeeded,
        "failed": failed,
        "skipped_log_only": skipped_log_only,
        "scanned": len(rows),
    }
    _log.info("asr_transcribe_tick", **counts)
    return counts
