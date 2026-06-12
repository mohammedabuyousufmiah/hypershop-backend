"""Async CRUD helpers for disputes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.disputes.codes import (
    ESCROW_STATUS_ACTIVE,
    LIVE_STATUSES,
    STATUS_AWAITING_SELLER,
)
from app.modules.disputes.models import (
    HypershopDispute,
    HypershopDisputeEvidence,
    HypershopDisputeMessage,
    HypershopEscrowHold,
)


# ─── Disputes ─────────────────────────────────────────────────────


async def create_dispute(
    session: AsyncSession, **fields: Any,
) -> HypershopDispute:
    row = HypershopDispute(**fields)
    session.add(row)
    await session.flush()
    return row


async def get_dispute(
    session: AsyncSession, dispute_id: UUID,
) -> HypershopDispute | None:
    return await session.get(HypershopDispute, dispute_id)


async def lock_dispute_for_update(
    session: AsyncSession, dispute_id: UUID,
) -> HypershopDispute | None:
    stmt = (
        select(HypershopDispute)
        .where(HypershopDispute.id == dispute_id)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalars().first()


async def update_dispute(
    session: AsyncSession, dispute_id: UUID, **fields: Any,
) -> HypershopDispute | None:
    fields["updated_at"] = datetime.now(timezone.utc)
    await session.execute(
        update(HypershopDispute)
        .where(HypershopDispute.id == dispute_id)
        .values(**fields)
    )
    return await session.get(HypershopDispute, dispute_id)


async def find_live_dispute_for_target(
    session: AsyncSession,
    *,
    order_id: UUID,
    order_item_id: UUID | None,
) -> HypershopDispute | None:
    """Return any live dispute for the same (order, order_item) tuple."""
    stmt = select(HypershopDispute).where(
        HypershopDispute.order_id == order_id,
        HypershopDispute.status.in_(LIVE_STATUSES),
    )
    if order_item_id is None:
        stmt = stmt.where(HypershopDispute.order_item_id.is_(None))
    else:
        stmt = stmt.where(HypershopDispute.order_item_id == order_item_id)
    return (await session.execute(stmt)).scalars().first()


async def list_disputes_by_buyer(
    session: AsyncSession,
    user_id: UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopDispute], int]:
    stmt = select(HypershopDispute).where(
        HypershopDispute.opened_by_user_id == user_id,
    )
    if status:
        stmt = stmt.where(HypershopDispute.status == status)
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(HypershopDispute.created_at.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def list_disputes_by_seller(
    session: AsyncSession,
    seller_id: UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopDispute], int]:
    stmt = select(HypershopDispute).where(
        HypershopDispute.seller_id == seller_id,
    )
    if status:
        stmt = stmt.where(HypershopDispute.status == status)
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(HypershopDispute.created_at.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def list_disputes_admin(
    session: AsyncSession,
    *,
    status: str | None = None,
    seller_id: UUID | None = None,
    mediator_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopDispute], int]:
    stmt = select(HypershopDispute)
    if status:
        stmt = stmt.where(HypershopDispute.status == status)
    if seller_id is not None:
        stmt = stmt.where(HypershopDispute.seller_id == seller_id)
    if mediator_id is not None:
        stmt = stmt.where(HypershopDispute.mediator_user_id == mediator_id)
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(HypershopDispute.created_at.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


# ─── Messages ─────────────────────────────────────────────────────


async def add_message(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    author_user_id: UUID | None,
    author_role: str,
    body: str,
    attachments: list[Any] | None = None,
    is_system: bool = False,
) -> HypershopDisputeMessage:
    row = HypershopDisputeMessage(
        dispute_id=dispute_id,
        author_user_id=author_user_id,
        author_role=author_role,
        body=body,
        attachments=attachments or [],
        is_system=is_system,
    )
    session.add(row)
    await session.flush()
    return row


async def list_messages(
    session: AsyncSession,
    dispute_id: UUID,
    *,
    limit: int = 100,
) -> list[HypershopDisputeMessage]:
    stmt = (
        select(HypershopDisputeMessage)
        .where(HypershopDisputeMessage.dispute_id == dispute_id)
        .order_by(HypershopDisputeMessage.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# ─── Evidence ─────────────────────────────────────────────────────


async def add_evidence(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    uploaded_by_user_id: UUID,
    uploader_role: str,
    file_url: str,
    content_type: str,
    size_bytes: int,
    description: str | None = None,
) -> HypershopDisputeEvidence:
    row = HypershopDisputeEvidence(
        dispute_id=dispute_id,
        uploaded_by_user_id=uploaded_by_user_id,
        uploader_role=uploader_role,
        file_url=file_url,
        content_type=content_type,
        size_bytes=size_bytes,
        description=description,
    )
    session.add(row)
    await session.flush()
    return row


async def list_evidence(
    session: AsyncSession, dispute_id: UUID,
) -> list[HypershopDisputeEvidence]:
    stmt = (
        select(HypershopDisputeEvidence)
        .where(HypershopDisputeEvidence.dispute_id == dispute_id)
        .order_by(HypershopDisputeEvidence.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_evidence_for_dispute(
    session: AsyncSession, dispute_id: UUID,
) -> int:
    stmt = select(func.count(HypershopDisputeEvidence.id)).where(
        HypershopDisputeEvidence.dispute_id == dispute_id,
    )
    return int((await session.execute(stmt)).scalar_one())


# ─── Escrow holds ─────────────────────────────────────────────────


async def create_escrow_hold(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    seller_id: UUID,
    order_id: UUID,
    held_amount_minor: int,
) -> HypershopEscrowHold:
    row = HypershopEscrowHold(
        dispute_id=dispute_id,
        seller_id=seller_id,
        order_id=order_id,
        held_amount_minor=held_amount_minor,
        status=ESCROW_STATUS_ACTIVE,
    )
    session.add(row)
    await session.flush()
    return row


async def get_escrow_for_dispute(
    session: AsyncSession, dispute_id: UUID,
) -> HypershopEscrowHold | None:
    stmt = select(HypershopEscrowHold).where(
        HypershopEscrowHold.dispute_id == dispute_id,
    )
    return (await session.execute(stmt)).scalars().first()


async def update_escrow_release(
    session: AsyncSession,
    hold_id: UUID,
    *,
    to_buyer_minor: int,
    to_seller_minor: int,
    new_status: str,
    note: str | None = None,
) -> HypershopEscrowHold | None:
    await session.execute(
        update(HypershopEscrowHold)
        .where(HypershopEscrowHold.id == hold_id)
        .values(
            released_to_buyer_minor=to_buyer_minor,
            released_to_seller_minor=to_seller_minor,
            status=new_status,
            release_note=note,
            released_at=datetime.now(timezone.utc),
        )
    )
    return await session.get(HypershopEscrowHold, hold_id)


async def list_active_escrow_holds(
    session: AsyncSession,
    *,
    seller_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopEscrowHold], int]:
    stmt = select(HypershopEscrowHold).where(
        HypershopEscrowHold.status == ESCROW_STATUS_ACTIVE,
    )
    if seller_id is not None:
        stmt = stmt.where(HypershopEscrowHold.seller_id == seller_id)
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(HypershopEscrowHold.created_at.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def count_active_holds_for_seller(
    session: AsyncSession, seller_id: UUID,
) -> int:
    stmt = select(func.count(HypershopEscrowHold.id)).where(
        HypershopEscrowHold.seller_id == seller_id,
        HypershopEscrowHold.status == ESCROW_STATUS_ACTIVE,
    )
    return int((await session.execute(stmt)).scalar_one())


async def count_held_amount_for_seller(
    session: AsyncSession, seller_id: UUID,
) -> int:
    stmt = select(func.coalesce(func.sum(
        HypershopEscrowHold.held_amount_minor,
    ), 0)).where(
        HypershopEscrowHold.seller_id == seller_id,
        HypershopEscrowHold.status == ESCROW_STATUS_ACTIVE,
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


async def find_overdue_seller_responses(
    session: AsyncSession, sla_hours: int,
) -> list[HypershopDispute]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=sla_hours)
    stmt = select(HypershopDispute).where(
        HypershopDispute.status == STATUS_AWAITING_SELLER,
        HypershopDispute.last_response_at.isnot(None),
        HypershopDispute.last_response_at < cutoff,
    )
    return list((await session.execute(stmt)).scalars().all())
