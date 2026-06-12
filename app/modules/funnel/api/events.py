"""POST /funnel/events/track — record a customer behavior event.

Gated by ``funnel.track`` permission on the caller's JWT. The actor's
``Principal`` is captured but not persisted per-event (volume too
high) — for forensics use the access log + ``request_id``.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.session import get_session
from app.core.security.principal import Principal
from app.modules.funnel.schemas import TrackEventRequest, TrackEventResponse
from app.modules.funnel.security import require_track
from app.modules.funnel.services.event_service import track_event

router = APIRouter()


@router.post("/track", response_model=TrackEventResponse)
async def track_customer_event(
    payload: TrackEventRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_track)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None
    return await track_event(db, payload, user_agent=user_agent, ip_address=ip_address)
