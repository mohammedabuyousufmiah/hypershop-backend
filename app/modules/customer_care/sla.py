"""SLA timer + breach detection.

`apply_sla_to_conversation()` — set due-at columns when a conversation is created.
`scan_breaches()` — worker entry point: marks breached conversations and emits
inbox events so dashboards/alerts can react.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import Conversation, SLAPolicy
from app.sse import publish_inbox_event

logger = logging.getLogger(__name__)


def _resolve_policy(db: Session, tenant_id: str, priority: str) -> SLAPolicy | None:
    return db.scalar(
        select(SLAPolicy).where(
            SLAPolicy.tenant_id == tenant_id,
            SLAPolicy.priority == priority,
            SLAPolicy.is_active.is_(True),
        )
    ) or db.scalar(
        select(SLAPolicy).where(
            SLAPolicy.tenant_id == tenant_id,
            SLAPolicy.priority == "normal",
            SLAPolicy.is_active.is_(True),
        )
    )


def apply_sla_to_conversation(db: Session, convo: Conversation) -> None:
    policy = _resolve_policy(db, convo.tenant_id, convo.priority)
    if not policy:
        return
    base = convo.created_at or datetime.utcnow()
    convo.sla_first_response_due_at = base + timedelta(minutes=policy.first_response_minutes)
    convo.sla_resolution_due_at = base + timedelta(minutes=policy.resolution_minutes)


def scan_breaches(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.utcnow()
    fr_breaches = _find_first_response_breaches(db, now)
    res_breaches = _find_resolution_breaches(db, now)
    counts = {"first_response_breaches": 0, "resolution_breaches": 0}
    for convo in fr_breaches:
        convo.sla_first_response_breached = True
        counts["first_response_breaches"] += 1
        publish_inbox_event(
            convo.agent_id,
            {
                "type": "sla.first_response_breach",
                "conversation_id": convo.id,
                "due_at": convo.sla_first_response_due_at.isoformat(),
            },
        )
    for convo in res_breaches:
        convo.sla_resolution_breached = True
        counts["resolution_breaches"] += 1
        publish_inbox_event(
            convo.agent_id,
            {
                "type": "sla.resolution_breach",
                "conversation_id": convo.id,
                "due_at": convo.sla_resolution_due_at.isoformat(),
            },
        )
    if counts["first_response_breaches"] or counts["resolution_breaches"]:
        db.commit()
    return counts


def _find_first_response_breaches(db: Session, now: datetime) -> Iterable[Conversation]:
    return db.scalars(
        select(Conversation).where(
            and_(
                Conversation.sla_first_response_due_at.is_not(None),
                Conversation.sla_first_response_due_at < now,
                Conversation.first_response_at.is_(None),
                Conversation.sla_first_response_breached.is_(False),
            )
        )
    ).all()


def _find_resolution_breaches(db: Session, now: datetime) -> Iterable[Conversation]:
    return db.scalars(
        select(Conversation).where(
            and_(
                Conversation.sla_resolution_due_at.is_not(None),
                Conversation.sla_resolution_due_at < now,
                Conversation.resolved_at.is_(None),
                Conversation.sla_resolution_breached.is_(False),
            )
        )
    ).all()
