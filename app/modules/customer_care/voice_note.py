"""WhatsApp voice-note channel: STT (incoming) + TTS (outgoing).

Pipeline
========
INBOUND (customer voice note):
  webhook → parse `audio` msg → enqueue voice-stt-queue
       ↓
  worker: download media bytes from Meta → Whisper STT
       → existing AI-reply pipeline (services.receive_incoming, but with
         transcribed text instead of raw text)
       → reply text generated and enqueued to voice-tts-queue

OUTBOUND (AI / agent voice reply):
  voice-tts-queue → worker: OpenAI TTS → upload to Meta → send audio msg

Vendor abstraction
==================
- STT: OpenAI Whisper (hosted) by default. ElevenLabs / local Whisper supported via
  `VOICE_NOTE_STT_PROVIDER`.
- TTS: OpenAI TTS by default; ElevenLabs supported via `VOICE_NOTE_TTS_PROVIDER`.
- Both providers fall back to dry-run mode when API keys are missing — never silently
  ship empty audio to a real customer in production (production refuses voice-note
  enqueue if provider unconfigured).

Bangla note
===========
Both Whisper and OpenAI TTS handle Bangla. ElevenLabs Multilingual v2 also supports
Bangla. Voice IDs are configurable.

Audio format
============
WhatsApp accepts:  audio/ogg (opus, voice-note PTT styling),
                   audio/mpeg (mp3), audio/aac, audio/amr, audio/mp4
We default to `audio/mpeg` (mp3) for portability. Set
`VOICE_NOTE_OUTBOUND_FORMAT=opus` if you want the WhatsApp voice-note UI (waveform).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    confidence: float
    provider: str
    raw: dict[str, Any] | None = None


@dataclass
class SynthesisResult:
    audio: bytes
    mime: str
    provider: str
    voice_flag_supported: bool  # mp3 → False, ogg/opus → True


# ─────────────────────────────────────────────────────────────────────
# STT
# ─────────────────────────────────────────────────────────────────────

async def transcribe(audio: bytes, mime: str = "audio/ogg") -> TranscriptionResult:
    cfg = settings()
    provider = (cfg.voice_note_stt_provider or "openai").lower()

    if provider == "openai":
        if not cfg.openai_api_key:
            if cfg.is_production and cfg.voice_note_required:
                raise RuntimeError(
                    "Voice-note STT required in production but OPENAI_API_KEY missing."
                )
            logger.warning("voice_note_stt_dry_run reason=no_openai_key")
            return TranscriptionResult(
                text="", language=None, confidence=0.0, provider="openai-dryrun"
            )
        return await _openai_whisper(audio, mime, cfg)

    if provider == "elevenlabs":
        if not cfg.elevenlabs_api_key:
            if cfg.is_production and cfg.voice_note_required:
                raise RuntimeError(
                    "Voice-note STT required in production but ELEVENLABS_API_KEY missing."
                )
            return TranscriptionResult(text="", language=None, confidence=0.0, provider="11labs-dryrun")
        return await _elevenlabs_stt(audio, mime, cfg)

    raise ValueError(f"Unknown STT provider: {provider}")


async def _openai_whisper(audio: bytes, mime: str, cfg) -> TranscriptionResult:
    files = {"file": ("voice.ogg", audio, mime)}
    data = {
        "model": cfg.voice_note_stt_model or "whisper-1",
        "response_format": "verbose_json",
    }
    if cfg.voice_note_default_language:
        data["language"] = cfg.voice_note_default_language
    async with httpx.AsyncClient(timeout=cfg.voice_note_stt_timeout_seconds) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {cfg.openai_api_key}"},
            files=files,
            data=data,
        )
        resp.raise_for_status()
        body = resp.json()
    return TranscriptionResult(
        text=(body.get("text") or "").strip(),
        language=body.get("language"),
        confidence=0.85,  # Whisper does not return per-clip confidence
        provider="openai-whisper",
        raw=body,
    )


async def _elevenlabs_stt(audio: bytes, mime: str, cfg) -> TranscriptionResult:
    files = {"file": ("voice.ogg", audio, mime)}
    data = {"model_id": cfg.voice_note_stt_model or "scribe_v1"}
    async with httpx.AsyncClient(timeout=cfg.voice_note_stt_timeout_seconds) as client:
        resp = await client.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": cfg.elevenlabs_api_key},
            files=files,
            data=data,
        )
        resp.raise_for_status()
        body = resp.json()
    return TranscriptionResult(
        text=(body.get("text") or "").strip(),
        language=body.get("language_code"),
        confidence=float(body.get("language_probability") or 0.85),
        provider="elevenlabs-scribe",
        raw=body,
    )


# ─────────────────────────────────────────────────────────────────────
# TTS
# ─────────────────────────────────────────────────────────────────────

async def synthesize(text: str, language: str | None = None) -> SynthesisResult:
    cfg = settings()
    provider = (cfg.voice_note_tts_provider or "openai").lower()
    fmt = (cfg.voice_note_outbound_format or "mp3").lower()

    if not text or not text.strip():
        raise ValueError("synthesize() called with empty text")

    if provider == "openai":
        if not cfg.openai_api_key:
            if cfg.is_production and cfg.voice_note_required:
                raise RuntimeError(
                    "Voice-note TTS required in production but OPENAI_API_KEY missing."
                )
            logger.warning("voice_note_tts_dry_run reason=no_openai_key")
            return SynthesisResult(audio=b"", mime="audio/mpeg", provider="openai-dryrun", voice_flag_supported=False)
        return await _openai_tts(text, fmt, cfg)

    if provider == "elevenlabs":
        if not cfg.elevenlabs_api_key:
            if cfg.is_production and cfg.voice_note_required:
                raise RuntimeError(
                    "Voice-note TTS required in production but ELEVENLABS_API_KEY missing."
                )
            return SynthesisResult(audio=b"", mime="audio/mpeg", provider="11labs-dryrun", voice_flag_supported=False)
        return await _elevenlabs_tts(text, fmt, cfg)

    raise ValueError(f"Unknown TTS provider: {provider}")


_OPENAI_FORMAT_MAP = {
    "mp3": ("mp3", "audio/mpeg", False),
    "opus": ("opus", "audio/ogg", True),
    "aac": ("aac", "audio/aac", False),
    "wav": ("wav", "audio/wav", False),
}


async def _openai_tts(text: str, fmt: str, cfg) -> SynthesisResult:
    fmt_key = fmt if fmt in _OPENAI_FORMAT_MAP else "mp3"
    response_format, mime, voice_flag = _OPENAI_FORMAT_MAP[fmt_key]
    payload = {
        "model": cfg.voice_note_tts_model or "tts-1",
        "voice": cfg.voice_note_tts_voice or "alloy",
        "input": text,
        "response_format": response_format,
    }
    async with httpx.AsyncClient(timeout=cfg.voice_note_tts_timeout_seconds) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {cfg.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        return SynthesisResult(
            audio=resp.content,
            mime=mime,
            provider="openai-tts",
            voice_flag_supported=voice_flag,
        )


async def _elevenlabs_tts(text: str, fmt: str, cfg) -> SynthesisResult:
    voice_id = cfg.voice_note_tts_voice or "21m00Tcm4TlvDq8ikWAM"  # Rachel
    output_format = "mp3_44100_128" if fmt == "mp3" else "ogg_22050_64"
    mime = "audio/mpeg" if fmt == "mp3" else "audio/ogg"
    voice_flag = (fmt == "opus")
    payload = {
        "text": text,
        "model_id": cfg.voice_note_tts_model or "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.7},
    }
    async with httpx.AsyncClient(timeout=cfg.voice_note_tts_timeout_seconds) as client:
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={output_format}",
            headers={"xi-api-key": cfg.elevenlabs_api_key, "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        return SynthesisResult(
            audio=resp.content,
            mime=mime,
            provider="elevenlabs-multilingual-v2",
            voice_flag_supported=voice_flag,
        )
