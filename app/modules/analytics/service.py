"""AnalyticsService — append-only ingest + paginated reads for admin."""
from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.models import AnalyticsEvent


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def track(
        self,
        *,
        anonymous_id: str,
        event_type: str,
        properties: dict,
        session_id: str | None,
        request_id: str | None,
        customer_user_id: UUID | None,
    ) -> AnalyticsEvent:
        row = AnalyticsEvent(
            anonymous_id=anonymous_id,
            customer_user_id=customer_user_id,
            event_type=event_type,
            properties=properties or {},
            session_id=session_id,
            request_id=request_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_admin(
        self,
        *,
        event_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[AnalyticsEvent], int]:
        base = select(AnalyticsEvent)
        if event_type:
            base = base.where(AnalyticsEvent.event_type == event_type)
        items = (
            await self.session.execute(
                base.order_by(AnalyticsEvent.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        total_stmt = select(func.count()).select_from(AnalyticsEvent)
        if event_type:
            total_stmt = total_stmt.where(AnalyticsEvent.event_type == event_type)
        total = int((await self.session.execute(total_stmt)).scalar_one())
        return items, total
