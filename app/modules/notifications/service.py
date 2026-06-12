"""Customer notifications service."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import CustomerNotification


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        customer_user_id: UUID,
        title: str,
        body: str,
        category: str = "general",
        action_url: str | None = None,
    ) -> CustomerNotification:
        row = CustomerNotification(
            customer_user_id=customer_user_id,
            category=category,
            title=title,
            body=body,
            action_url=action_url,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        unread_only: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[CustomerNotification], int, int]:
        base = select(CustomerNotification).where(
            CustomerNotification.customer_user_id == user_id
        )
        if unread_only:
            base = base.where(CustomerNotification.is_read.is_(False))
        items = (
            await self.session.execute(
                base.order_by(CustomerNotification.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        total = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(CustomerNotification)
                    .where(CustomerNotification.customer_user_id == user_id)
                )
            ).scalar_one()
        )
        unread = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(CustomerNotification)
                    .where(
                        CustomerNotification.customer_user_id == user_id,
                        CustomerNotification.is_read.is_(False),
                    )
                )
            ).scalar_one()
        )
        return items, total, unread

    async def mark_read(
        self,
        *,
        user_id: UUID,
        ids: list[UUID] | None,
        all_unread: bool,
    ) -> int:
        if not all_unread and not ids:
            return 0
        now = datetime.now(timezone.utc)
        stmt = update(CustomerNotification).where(
            CustomerNotification.customer_user_id == user_id,
            CustomerNotification.is_read.is_(False),
        )
        if not all_unread:
            stmt = stmt.where(CustomerNotification.id.in_(ids or []))
        stmt = stmt.values(is_read=True, read_at=now)
        result = await self.session.execute(stmt)
        return result.rowcount or 0
