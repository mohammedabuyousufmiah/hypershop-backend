"""RAG-augmented AI reply.

Replaces the keyword-based product lookup with:
  1. Vector retrieval against tenant KB (top-k chunks)
  2. OpenAI chat completion conditioned on retrieved context
  3. Citation list of which chunks informed the answer

Falls back gracefully:
- No KB chunks indexed → returns None so caller can use legacy product-search reply.
- OpenAI not configured → returns None (caller falls back).
- Low retrieval scores (below RAG_MIN_SCORE) → returns None.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.rag.retrieval import RetrievedChunk, format_context, retrieve

logger = logging.getLogger(__name__)


@dataclass
class RagReply:
    text: str
    confidence: Decimal
    citations: list[str]  # chunk_ids the LLM was given
    used_chunks: list[RetrievedChunk]


_SYSTEM_PROMPT_BANGLA = (
    "তুমি একটি বাংলা কাস্টমার কেয়ার সহকারী। শুধুমাত্র নিচের REFERENCE TEXT-এ "
    "যা আছে সেই তথ্য দিয়ে উত্তর দাও। REFERENCE-এ না থাকলে সরাসরি বলো "
    "'আমি নিশ্চিত নই, একজন প্রতিনিধি কিছুক্ষণের মধ্যে আপনাকে সাহায্য করবেন।' "
    "কখনো দাম, স্টক, ডেলিভারি সময়, বা পলিসি বানিয়ে বলবে না। উত্তর সংক্ষিপ্ত "
    "(২-৩ বাক্য), ভদ্র এবং পরিষ্কার রাখো।"
)
_SYSTEM_PROMPT_ENGLISH = (
    "You are a customer care assistant. Answer ONLY using facts present in the "
    "REFERENCE TEXT below. If the reference does not contain the answer, reply "
    "exactly: 'I'm not sure, a representative will assist you shortly.' Never "
    "invent prices, stock, delivery time, or policy. Keep replies short "
    "(2-3 sentences), polite, and clear."
)


async def rag_reply(
    db: Session,
    *,
    customer_text: str,
    customer_language: str,
) -> RagReply | None:
    cfg = settings()
    if not cfg.rag_enabled:
        return None
    if not customer_text.strip():
        return None

    chunks = await retrieve(
        db, customer_text, k=cfg.rag_retrieval_top_k, min_score=cfg.rag_min_score
    )
    if not chunks:
        return None

    context = format_context(chunks, max_chars=cfg.rag_context_max_chars)
    if not context:
        return None

    if not cfg.openai_api_key:
        if cfg.is_production and cfg.rag_required:
            logger.error("rag_reply_no_openai_key_in_production")
        return None

    is_english = customer_language == "english" or customer_language == "en"
    system = _SYSTEM_PROMPT_ENGLISH if is_english else _SYSTEM_PROMPT_BANGLA
    user_msg = (
        f"REFERENCE TEXT:\n{context}\n\nCUSTOMER MESSAGE: {customer_text.strip()}"
    )

    try:
        async with httpx.AsyncClient(timeout=cfg.rag_chat_timeout_seconds) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.rag_chat_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception:
        logger.exception("rag_reply_chat_completion_failed")
        return None

    try:
        text = body["choices"][0]["message"]["content"].strip()
    except Exception:
        return None

    if not text:
        return None

    # Confidence proxy: highest retrieval score, capped to [0.7, 0.95]
    top_score = chunks[0].score if chunks else 0.0
    conf = max(0.7, min(0.95, top_score))

    return RagReply(
        text=text,
        confidence=Decimal(str(round(conf, 2))),
        citations=[c.chunk_id for c in chunks],
        used_chunks=chunks,
    )
