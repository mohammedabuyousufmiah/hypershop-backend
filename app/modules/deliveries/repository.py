from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.modules.deliveries.models import (
    DeliveryAssignment,
    DeliveryStatusHistory,
    Rider,
)


class RiderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, rider_id: UUID) -> Rider | None:
        return await self.session.get(Rider, rider_id)

    async def get_by_code(self, code: str) -> Rider | None:
        return (
            await self.session.execute(select(Rider).where(Rider.code == code))
        ).scalar_one_or_none()

    async def code_exists(self, code: str) -> bool:
        return (
            await self.session.execute(select(Rider.id).where(Rider.code == code))
        ).scalar_one_or_none() is not None

    async def get_by_linked_user(self, user_id: UUID) -> Rider | None:
        return (
            await self.session.execute(
                select(Rider).where(Rider.linked_user_id == user_id),
            )
        ).scalar_one_or_none()

    async def list_all(self, *, active_only: bool = False) -> Sequence[Rider]:
        stmt = select(Rider).order_by(Rider.name)
        if active_only:
            stmt = stmt.where(Rider.is_active.is_(True))
        return (await self.session.execute(stmt)).scalars().all()

    async def create(self, **fields: Any) -> Rider:
        r = Rider(**fields)
        self.session.add(r)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError(
                "Rider code or linked_user_id already exists.",
            ) from e
        return r

    async def update(self, rider_id: UUID, **fields: Any) -> Rider:
        r = await self.session.get(Rider, rider_id)
        if r is None:
            raise NotFoundError("Rider not found.")
        for k, v in fields.items():
            if v is not None:
                setattr(r, k, v)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Rider uniqueness violation.") from e
        return r


class DeliveryAssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, assignment_id: UUID) -> DeliveryAssignment | None:
        stmt = (
            select(DeliveryAssignment)
            .where(DeliveryAssignment.id == assignment_id)
            .options(selectinload(DeliveryAssignment.history))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_locked(self, assignment_id: UUID) -> DeliveryAssignment | None:
        stmt = (
            select(DeliveryAssignment)
            .where(DeliveryAssignment.id == assignment_id)
            .with_for_update()
        )
        a = (await self.session.execute(stmt)).scalar_one_or_none()
        if a is None:
            return None
        await self.session.refresh(a, attribute_names=("history",))
        return a

    async def create(self, **fields: Any) -> DeliveryAssignment:
        a = DeliveryAssignment(**fields)
        self.session.add(a)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError(
                "An active delivery assignment already exists for this order.",
            ) from e
        return a

    async def add_history(
        self,
        *,
        assignment_id: UUID,
        from_status: str | None,
        to_status: str,
        transitioned_by: UUID | None,
        reason: str | None,
    ) -> DeliveryStatusHistory:
        h = DeliveryStatusHistory(
            assignment_id=assignment_id,
            from_status=from_status,
            to_status=to_status,
            transitioned_by=transitioned_by,
            reason=reason,
        )
        self.session.add(h)
        await self.session.flush()
        return h

    async def list_admin(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
        rider_id: UUID | None = None,
    ) -> tuple[Sequence[DeliveryAssignment], int]:
        conds: list[Any] = []
        if status is not None:
            conds.append(DeliveryAssignment.status == status)
        if rider_id is not None:
            conds.append(DeliveryAssignment.rider_id == rider_id)
        count_stmt = select(func.count()).select_from(DeliveryAssignment)
        list_stmt = (
            select(DeliveryAssignment)
            .order_by(DeliveryAssignment.assigned_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (await self.session.execute(list_stmt)).scalars().all()
        return rows, int(total)

    async def list_for_rider(
        self,
        *,
        rider_id: UUID,
        offset: int,
        limit: int,
        status: str | None = None,
    ) -> tuple[Sequence[DeliveryAssignment], int]:
        conds: list[Any] = [DeliveryAssignment.rider_id == rider_id]
        if status is not None:
            conds.append(DeliveryAssignment.status == status)
        count_stmt = (
            select(func.count()).select_from(DeliveryAssignment).where(*conds)
        )
        list_stmt = (
            select(DeliveryAssignment)
            .where(*conds)
            .order_by(DeliveryAssignment.assigned_at.desc())
            .offset(offset)
            .limit(limit)
        )
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (await self.session.execute(list_stmt)).scalars().all()
        return rows, int(total)


def require_rider(r: Rider | None) -> Rider:
    if r is None:
        raise NotFoundError("Rider not found.")
    return r


def require_assignment(a: DeliveryAssignment | None) -> DeliveryAssignment:
    if a is None:
        raise NotFoundError("Delivery assignment not found.")
    return a
