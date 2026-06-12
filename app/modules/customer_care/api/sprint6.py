"""Sprint 6 routes — AI-native enhancements (Module 47).

Adds 8 routes:
- POST /conversations/{id}/suggest        — draft 1-3 reply suggestions
- POST /messages/{id}/classify            — explicit re-classify (sentiment + intent)
- POST /messages/{id}/translate           — translate message to target language
- POST /conversations/{id}/summary        — manually trigger summary (also auto on /resolve)
- GET  /reports/topic-trends              — intent counts over a window
- GET  /reports/volume-anomaly            — flag days where volume > 2σ above 7d avg
- GET  /messages/{id}/citations           — fetch the KB chunks an AI msg cited
- GET  /sentiment/timeline                — per-day sentiment trend
"""
from __future__ import annotations

import math
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import Field
from sqlalchemy import text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.customer_care import ai

_AGENT = "customercare.agent"
_ADMIN = "customercare.admin"

router = APIRouter(tags=["customer-care-sprint6"])
_log = get_logger("hypershop.customer_care.sprint6")


# ============================================================== Suggest
class SuggestRequest(StrictModel):
    n: int = Field(default=3, ge=1, le=5)
    language: str = Field(default="english", pattern=r"^(english|bangla|hindi)$")


@router.post(
    "/conversations/{conv_id}/suggest",
    summary="AI drafts N reply suggestions for the agent",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def conversation_suggest(
    conv_id: Annotated[UUID, Path(...)],
    body: SuggestRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    "SELECT sender_type, message_body FROM cc_messages "
                    "WHERE conversation_id = :c ORDER BY created_at DESC LIMIT 20"
                ),
                {"c": conv_id},
            )
        ).all()
        if not rows:
            raise NotFoundError("Conversation has no messages")
        # Reverse to chronological order
        transcript = [
            {"sender_type": r[0], "body": r[1] or ""}
            for r in reversed(rows)
        ]
    suggestions = await ai.suggest_replies(
        transcript, n=body.n, language=body.language,
    )
    if suggestions is None:
        return {
            "ok": False,
            "reason": "ai_unavailable",
            "suggestions": [
                "Thanks for your message — we'll get back to you shortly.",
                "Could you share more details about the issue?",
                "An agent will be with you in a moment.",
            ][: body.n],
        }
    return {"ok": True, "suggestions": suggestions}


# ============================================================== Classify on demand
@router.post(
    "/messages/{msg_id}/classify",
    summary="Run sentiment + intent classification on this message (persists result)",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def classify_message(
    msg_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t("SELECT message_body FROM cc_messages WHERE id = :m"),
                {"m": msg_id},
            )
        ).first()
        if r is None:
            raise NotFoundError("Message not found")
        text = r[0] or ""
    result = await ai.classify_message(text)
    if result is None:
        return {"ok": False, "reason": "ai_unavailable_or_empty"}
    async with uow.transactional() as session:
        await session.execute(
            _t(
                "UPDATE cc_messages SET sentiment = :s, sentiment_score = :sc, "
                "intent_tag = :it WHERE id = :m"
            ),
            {
                "s": result["sentiment"], "sc": result["sentiment_score"],
                "it": result["intent_tag"], "m": msg_id,
            },
        )
    return {"ok": True, **result}


# ============================================================== Translate
class TranslateRequest(StrictModel):
    target_language: str = Field(..., pattern=r"^(english|bangla|hindi|arabic|urdu)$")


@router.post(
    "/messages/{msg_id}/translate",
    summary="Translate this message to target_language; result cached on the row",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def translate_message(
    msg_id: Annotated[UUID, Path(...)],
    body: TranslateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t(
                    "SELECT message_body, translated_body, translated_language "
                    "FROM cc_messages WHERE id = :m"
                ),
                {"m": msg_id},
            )
        ).first()
        if r is None:
            raise NotFoundError("Message not found")
        original, cached, cached_lang = r[0] or "", r[1], r[2]
    if cached and cached_lang == body.target_language[:8]:
        return {
            "ok": True, "cached": True,
            "translated_body": cached,
            "translated_language": cached_lang,
        }
    translated = await ai.translate_text(original, target_language=body.target_language)
    if translated is None:
        return {"ok": False, "reason": "ai_unavailable"}
    async with uow.transactional() as session:
        await session.execute(
            _t(
                "UPDATE cc_messages SET translated_body = :t, translated_language = :l "
                "WHERE id = :m"
            ),
            {"t": translated, "l": body.target_language[:8], "m": msg_id},
        )
    return {
        "ok": True, "cached": False,
        "translated_body": translated,
        "translated_language": body.target_language[:8],
    }


# ============================================================== Summary
@router.post(
    "/conversations/{conv_id}/summary",
    summary="Generate (or regenerate) an AI summary of this conversation",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def conversation_summarize(
    conv_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    "SELECT sender_type, message_body FROM cc_messages "
                    "WHERE conversation_id = :c ORDER BY created_at ASC LIMIT 100"
                ),
                {"c": conv_id},
            )
        ).all()
        if not rows:
            raise NotFoundError("Conversation has no messages")
        transcript = [{"sender_type": r[0], "body": r[1] or ""} for r in rows]
    summary = await ai.summarize_conversation(transcript)
    if summary is None:
        return {"ok": False, "reason": "ai_unavailable"}
    async with uow.transactional() as session:
        await session.execute(
            _t(
                "UPDATE cc_conversations SET ai_summary = :s, "
                "summary_generated_at = now() WHERE id = :c"
            ),
            {"s": summary, "c": conv_id},
        )
        await record_audit(
            actor=principal,
            action="customer_care.conversation.summarized",
            resource_type="cc_conversation",
            resource_id=conv_id,
        )
    return {"ok": True, "summary": summary}


# ============================================================== Topic trends
@router.get(
    "/reports/topic-trends",
    summary="Intent-tag counts over a window — what are customers asking about this week?",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def topic_trends(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT intent_tag, COUNT(*) AS n
                    FROM cc_messages
                    WHERE sender_type = 'customer'
                      AND intent_tag IS NOT NULL
                      AND created_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY intent_tag
                    ORDER BY n DESC
                    """,
                )
            )
        ).all()
        total = (
            await session.execute(
                _t(
                    f"""SELECT COUNT(*) FROM cc_messages
                        WHERE sender_type = 'customer'
                        AND created_at >= now() - INTERVAL '{int(days)} days'"""
                )
            )
        ).scalar_one()
    return {
        "window_days": days,
        "total_inbound": int(total or 0),
        "tagged_count": sum(int(r[1]) for r in rows),
        "topics": [{"intent_tag": r[0], "count": int(r[1])} for r in rows],
    }


# ============================================================== Volume anomaly
@router.get(
    "/reports/volume-anomaly",
    summary="Flag days where inbound volume > 2σ above the rolling 30-day mean",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def volume_anomaly(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=7, le=180),
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT DATE(created_at) AS day, COUNT(*) AS n
                    FROM cc_messages
                    WHERE sender_type = 'customer'
                      AND created_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY 1 ORDER BY 1
                    """,
                )
            )
        ).all()
    counts = [int(r[1]) for r in rows]
    if not counts:
        return {"window_days": days, "mean": 0, "stddev": 0, "anomalies": [], "daily": []}
    mean = sum(counts) / len(counts)
    var = sum((x - mean) ** 2 for x in counts) / len(counts)
    sd = math.sqrt(var) if var > 0 else 0.0
    threshold = mean + 2 * sd
    daily = [
        {"day": str(r[0]), "count": int(r[1]),
         "is_anomaly": int(r[1]) > threshold}
        for r in rows
    ]
    anomalies = [d for d in daily if d["is_anomaly"]]
    return {
        "window_days": days,
        "mean": round(mean, 2),
        "stddev": round(sd, 2),
        "threshold": round(threshold, 2),
        "anomalies": anomalies,
        "daily": daily,
    }


# ============================================================== Citations
@router.get(
    "/messages/{msg_id}/citations",
    summary="Fetch the KB chunks an AI reply cited (if any)",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def message_citations(
    msg_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t(
                    "SELECT rag_citation_ids FROM cc_messages WHERE id = :m"
                ),
                {"m": msg_id},
            )
        ).first()
        if r is None:
            raise NotFoundError("Message not found")
        ids = r[0] or []
        if not ids:
            return {"message_id": str(msg_id), "citations": []}
        chunks = (
            await session.execute(
                _t(
                    "SELECT c.id, c.text, c.position, d.title, d.id AS doc_id "
                    "FROM cc_knowledge_chunks c "
                    "JOIN cc_knowledge_documents d ON d.id = c.document_id "
                    "WHERE c.id = ANY(:ids)"
                ),
                {"ids": ids},
            )
        ).all()
    return {
        "message_id": str(msg_id),
        "citations": [
            {
                "chunk_id": str(c[0]), "text": c[1][:500],
                "position": c[2], "document_title": c[3],
                "document_id": str(c[4]),
            }
            for c in chunks
        ],
    }


# ============================================================== Sentiment timeline
@router.get(
    "/sentiment/timeline",
    summary="Daily sentiment trend over a window",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def sentiment_timeline(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=1, le=180),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT DATE(created_at) AS day,
                           COUNT(*) FILTER (WHERE sentiment = 'positive') AS pos,
                           COUNT(*) FILTER (WHERE sentiment = 'neutral')  AS neu,
                           COUNT(*) FILTER (WHERE sentiment = 'negative') AS neg,
                           AVG(sentiment_score)::float AS avg_score
                    FROM cc_messages
                    WHERE sender_type = 'customer'
                      AND sentiment IS NOT NULL
                      AND created_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY 1 ORDER BY 1
                    """,
                )
            )
        ).all()
        return [
            {
                "day": str(r[0]),
                "positive": int(r[1] or 0),
                "neutral": int(r[2] or 0),
                "negative": int(r[3] or 0),
                "avg_score": round(float(r[4]), 3) if r[4] is not None else None,
            }
            for r in rows
        ]
