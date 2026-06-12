"""GET /funnel/customers* — list scored customers + hot leads + tasks.

All read-only; gated by ``funnel.view`` permission on the caller's JWT.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_session
from app.core.security.principal import Principal
from app.modules.funnel.models import FunnelCustomer, FunnelFollowUpTask
from app.modules.funnel.security import require_view
from app.modules.funnel.services.privacy import customer_to_safe_dict

router = APIRouter()


@router.get("")
@router.get("/")
async def list_customers(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    segment: str | None = None,
    min_score: int | None = Query(default=None),
) -> list[dict]:
    stmt = select(FunnelCustomer).where(FunnelCustomer.deleted_at.is_(None))
    if segment:
        stmt = stmt.where(FunnelCustomer.segment == segment)
    if min_score is not None:
        stmt = stmt.where(FunnelCustomer.current_score >= min_score)
    stmt = stmt.order_by(FunnelCustomer.current_score.desc()).limit(200)
    rows = (await db.execute(stmt)).scalars().all()
    return [customer_to_safe_dict(c) for c in rows]


@router.get("/hot-leads")
async def hot_leads(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    stmt = (
        select(FunnelCustomer)
        .where(
            FunnelCustomer.deleted_at.is_(None),
            FunnelCustomer.current_score >= 76,
        )
        .order_by(FunnelCustomer.current_score.desc())
        .limit(200)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [customer_to_safe_dict(c) for c in rows]


@router.get("/followup-tasks")
async def followup_tasks(
    principal: Annotated[Principal, Depends(require_view)],
    db: Annotated[AsyncSession, Depends(get_session)],
    status: str = "pending",
) -> list[dict]:
    stmt = (
        select(FunnelFollowUpTask)
        .where(FunnelFollowUpTask.status == status)
        .order_by(FunnelFollowUpTask.created_at.desc())
        .limit(200)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": t.id,
            "customer_id": t.customer_id,
            "channel": t.channel,
            "reason": t.reason,
            "message_template_key": t.message_template_key,
            "status": t.status,
            "blocked_reason": t.blocked_reason,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "sent_at": t.sent_at.isoformat() if t.sent_at else None,
        }
        for t in rows
    ]
