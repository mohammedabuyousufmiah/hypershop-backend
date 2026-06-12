"""CSAT survey: triggered on conversation resolution.

Flow
----
1. `start_csat(conversation_id)` — admin/agent or auto-triggered when status="resolved".
2. Survey row created with a unique token; outbound message dispatched via the
   conversation's channel (WhatsApp template) with `?token=...` link or quick-reply 1-5.
3. `submit_csat(token, score, comment)` — public endpoint, no auth.
4. Reports aggregate by agent / time window.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import CSATSurvey, Conversation


def start_csat(db: Session, conversation_id: str) -> CSATSurvey:
    convo = db.get(Conversation, conversation_id)
    if not convo:
        raise ValueError("conversation_not_found")
    survey = CSATSurvey(
        tenant_id=convo.tenant_id,
        conversation_id=convo.id,
        customer_id=convo.customer_id,
        agent_id=convo.agent_id,
        survey_token=secrets.token_urlsafe(24),
        sent_at=datetime.utcnow(),
        status="sent",
    )
    db.add(survey)
    db.commit()
    db.refresh(survey)
    return survey


def submit_csat(
    db: Session, *, token: str, score: int, comment: str | None
) -> CSATSurvey:
    if not (1 <= score <= 5):
        raise ValueError("score_out_of_range_1_5")
    survey = db.scalar(select(CSATSurvey).where(CSATSurvey.survey_token == token))
    if not survey:
        raise ValueError("survey_not_found")
    if survey.status == "responded":
        raise ValueError("already_responded")
    survey.score = score
    survey.comment = (comment or "")[:2000] or None
    survey.responded_at = datetime.utcnow()
    survey.status = "responded"
    db.commit()
    db.refresh(survey)
    return survey


def csat_summary(db: Session, days: int = 30) -> dict[str, Any]:
    rows = db.execute(
        select(
            func.count(CSATSurvey.id),
            func.avg(CSATSurvey.score),
            func.sum(case((CSATSurvey.score >= 4, 1), else_=0)),
        ).where(CSATSurvey.status == "responded")
    ).first()
    total, avg, top_box = rows or (0, None, 0)
    return {
        "responses": int(total or 0),
        "avg_score": round(float(avg), 2) if avg is not None else None,
        "csat_top_box_pct": round(float(top_box or 0) * 100.0 / float(total), 1)
        if total
        else None,
        "window_days": days,
    }
