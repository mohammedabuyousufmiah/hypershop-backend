from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.modules.orders.models import (
    Order,
    OrderFulfillmentStageHistory,
    OrderLine,
    OrderStatusHistory,
)


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, order_id: UUID) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.lines), selectinload(Order.history))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_locked(self, order_id: UUID) -> Order | None:
        """Fetch the order header with ``SELECT ... FOR UPDATE`` so concurrent
        transitions on the same order serialize.
        """
        stmt = select(Order).where(Order.id == order_id).with_for_update()
        order = (await self.session.execute(stmt)).scalar_one_or_none()
        if order is None:
            return None
        # Eager-load lines + history after the lock is taken.
        await self.session.refresh(order, attribute_names=("lines", "history"))
        return order

    async def code_exists(self, code: str) -> bool:
        return (
            await self.session.execute(select(Order.id).where(Order.code == code))
        ).scalar_one_or_none() is not None

    async def create(self, **fields: Any) -> Order:
        order = Order(**fields)
        self.session.add(order)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Order code collision.") from e
        return order

    async def add_line(self, **fields: Any) -> OrderLine:
        line = OrderLine(**fields)
        self.session.add(line)
        await self.session.flush()
        return line

    async def add_history(
        self,
        *,
        order_id: UUID,
        from_status: str | None,
        to_status: str,
        transitioned_by: UUID | None,
        reason: str | None,
    ) -> OrderStatusHistory:
        h = OrderStatusHistory(
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            transitioned_by=transitioned_by,
            reason=reason,
        )
        self.session.add(h)
        await self.session.flush()
        return h

    async def add_stage_history(
        self,
        *,
        order_id: UUID,
        from_stage: str | None,
        to_stage: str,
        transitioned_by: UUID | None,
        reason: str | None,
        meta: dict | None = None,
    ) -> OrderFulfillmentStageHistory:
        """Append a fulfillment_stage transition row.

        Must be called inside an open transaction. The migration REVOKEs
        UPDATE/DELETE so the row is immutable once committed.
        """
        h = OrderFulfillmentStageHistory(
            order_id=order_id,
            from_stage=from_stage,
            to_stage=to_stage,
            transitioned_by=transitioned_by,
            reason=reason,
            meta=meta,
        )
        self.session.add(h)
        await self.session.flush()
        return h

    async def list_for_customer(
        self,
        *,
        customer_user_id: UUID,
        offset: int,
        limit: int,
        status: str | None = None,
    ) -> tuple[Sequence[Order], int]:
        conds: list[Any] = [Order.customer_user_id == customer_user_id]
        if status is not None:
            conds.append(Order.status == status)
        count_stmt = select(func.count()).select_from(Order).where(*conds)
        list_stmt = (
            select(Order)
            .where(*conds)
            .order_by(Order.placed_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(Order.lines))
        )
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (await self.session.execute(list_stmt)).scalars().all()
        return rows, int(total)

    async def list_admin(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
        customer_user_id: UUID | None = None,
    ) -> tuple[Sequence[Order], int]:
        conds: list[Any] = []
        if status is not None:
            conds.append(Order.status == status)
        if customer_user_id is not None:
            conds.append(Order.customer_user_id == customer_user_id)
        count_stmt = select(func.count()).select_from(Order)
        list_stmt = (
            select(Order)
            .order_by(Order.placed_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(Order.lines))
        )
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (await self.session.execute(list_stmt)).scalars().all()
        return rows, int(total)


def require_order(order: Order | None) -> Order:
    if order is None:
        raise NotFoundError("Order not found.")
    return order
