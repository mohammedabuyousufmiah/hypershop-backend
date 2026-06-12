"""Public analytics ingest — POST /analytics/track."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_optional_principal
from app.core.security.principal import Principal
from app.modules.analytics.schemas import AnalyticsTrackAck, AnalyticsTrackIn
from app.modules.analytics.service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post(
    "/track",
    response_model=AnalyticsTrackAck,
    summary="Ingest a browser/app event (no auth required)",
)
async def track(
    body: AnalyticsTrackIn,
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[
        Principal | None, Depends(get_optional_principal)
    ] = None,
) -> AnalyticsTrackAck:
    request_id = request.headers.get("x-request-id") or None
    async with uow.transactional() as session:
        svc = AnalyticsService(session)
        row = await svc.track(
            anonymous_id=body.anonymous_id,
            event_type=body.event_type,
            properties=body.properties,
            session_id=body.session_id,
            request_id=request_id,
            customer_user_id=principal.user_id if principal else None,
        )
    return AnalyticsTrackAck(
        id=row.id,
        accepted_at=datetime.now(timezone.utc),
    )
