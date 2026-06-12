"""AI helpers — OpenAI-backed classification, summarization, translation, suggestion.

All functions degrade gracefully (return ``None``) when ``OPENAI_API_KEY``
is missing or the call fails. Callers must accept ``None`` and skip
the side-effect — never block the user-facing path on an AI call.

Designed to be cheap + fast:
- Classification uses ``gpt-4o-mini`` with strict JSON-mode response.
- Summarization uses the same model.
- Translation uses the same model.

For high-volume traffic, swap to async batching or a smaller fine-tuned
classifier — that's a phase-7 optimization, not a v9 concern.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx

from app.core.logging import get_logger
from app.modules.customer_care.config import settings

_log = get_logger("hypershop.customer_care.ai")

# ============================================================== shared
async def _chat_json(prompt_system: str, prompt_user: str, *, timeout: float = 20.0) -> dict | None:
    """Call OpenAI Chat in JSON-mode. Returns parsed dict or ``None``."""
    cfg = settings()
    if not cfg.openai_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.openai_model,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": prompt_system},
                        {"role": "user", "content": prompt_user},
                    ],
                },
            )
            r.raise_for_status()
            data = r.json()
            content = ((data.get("choices") or [{}])[0]
                       .get("message", {}).get("content", "")) or "{}"
            return json.loads(content)
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        _log.warning("ai_chat_json_failed", error=str(e))
        return None


# ============================================================== Classify
INTENT_TAGS = ("refund", "cancel", "shipping", "tracking", "pre_sales",
               "complaint", "compliment", "tech_support", "billing", "other")


async def classify_message(text: str) -> dict | None:
    """Return ``{"sentiment": "positive|neutral|negative",
                 "sentiment_score": -1.0..1.0,
                 "intent_tag": one_of(INTENT_TAGS)}``
    or ``None`` if AI unavailable.
    """
    if not text or not text.strip():
        return None
    sys = (
        "You classify e-commerce customer messages. "
        "Return ONLY JSON with keys: "
        "sentiment ('positive'|'neutral'|'negative'), "
        "sentiment_score (float, -1.0 to 1.0), "
        f"intent_tag (one of {', '.join(INTENT_TAGS)}). "
        "If unsure, choose 'neutral' and 'other'."
    )
    out = await _chat_json(sys, text[:2000])
    if not out:
        return None
    sentiment = out.get("sentiment", "neutral")
    if sentiment not in ("positive", "neutral", "negative"):
        sentiment = "neutral"
    try:
        score = float(out.get("sentiment_score", 0.0))
        score = max(-1.0, min(1.0, score))
    except (TypeError, ValueError):
        score = 0.0
    intent = out.get("intent_tag", "other")
    if intent not in INTENT_TAGS:
        intent = "other"
    return {"sentiment": sentiment, "sentiment_score": score, "intent_tag": intent}


# ============================================================== Summarize
async def summarize_conversation(transcript: list[dict[str, str]]) -> str | None:
    """Given a list of ``{"sender_type": "customer|agent|ai|system",
    "body": "..."}`` messages, produce a 2-4 sentence summary.
    """
    if not transcript:
        return None
    cfg = settings()
    if not cfg.openai_api_key:
        return None
    convo_text = "\n".join(
        f"[{m['sender_type']}] {(m.get('body') or '')[:600]}"
        for m in transcript[-40:]  # cap at last 40 turns
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.openai_model,
                    "temperature": 0.2,
                    "max_tokens": 220,
                    "messages": [
                        {"role": "system", "content": (
                            "Summarize this customer-support conversation in "
                            "2-4 sentences. Focus on: customer's issue, "
                            "resolution, any next action needed. Plain text, "
                            "no markdown."
                        )},
                        {"role": "user", "content": convo_text},
                    ],
                },
            )
            r.raise_for_status()
            data = r.json()
            return ((data.get("choices") or [{}])[0]
                    .get("message", {}).get("content") or "").strip() or None
    except httpx.HTTPError as e:
        _log.warning("ai_summarize_failed", error=str(e))
        return None


# ============================================================== Translate
async def translate_text(text: str, *, target_language: str) -> str | None:
    """Translate to target language (e.g. 'english', 'bangla', 'hindi').
    Returns translated text or ``None`` if AI unavailable.
    """
    if not text or not text.strip():
        return None
    sys = (
        f"Translate the user's message to {target_language}. "
        "Reply with ONLY the translation, no quotes, no explanation. "
        "Preserve tone. Keep proper nouns + currency unchanged."
    )
    cfg = settings()
    if not cfg.openai_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.openai_model,
                    "temperature": 0.1, "max_tokens": 320,
                    "messages": [
                        {"role": "system", "content": sys},
                        {"role": "user", "content": text[:2000]},
                    ],
                },
            )
            r.raise_for_status()
            data = r.json()
            return ((data.get("choices") or [{}])[0]
                    .get("message", {}).get("content") or "").strip() or None
    except httpx.HTTPError as e:
        _log.warning("ai_translate_failed", error=str(e))
        return None


# ============================================================== Suggest replies
async def suggest_replies(
    transcript: list[dict[str, str]],
    *,
    n: int = 3,
    language: str = "english",
) -> list[str] | None:
    """Draft N agent reply suggestions for the agent to pick from."""
    if not transcript:
        return None
    cfg = settings()
    if not cfg.openai_api_key:
        return None
    convo = "\n".join(
        f"[{m['sender_type']}] {(m.get('body') or '')[:600]}"
        for m in transcript[-12:]
    )
    sys = (
        f"You're drafting reply options for a Bangladesh e-commerce customer "
        f"support agent. Output {n} short distinct reply suggestions in "
        f"{language}, separated by '\\n---\\n'. Each suggestion under 280 "
        "chars. Be warm, concise, and specific to the conversation context."
    )
    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.openai_model,
                    "temperature": 0.5, "max_tokens": 600,
                    "messages": [
                        {"role": "system", "content": sys},
                        {"role": "user", "content": convo},
                    ],
                },
            )
            r.raise_for_status()
            data = r.json()
            content = ((data.get("choices") or [{}])[0]
                       .get("message", {}).get("content") or "").strip()
            parts = [p.strip() for p in content.split("---") if p.strip()]
            return parts[:n] if parts else None
    except httpx.HTTPError as e:
        _log.warning("ai_suggest_failed", error=str(e))
        return None
