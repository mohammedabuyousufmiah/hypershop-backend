"""Repository — async CRUD against the 4 cc_inbox tables."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customer_care.cc_inbox_models import (
    CCMessage,
    CCThread,
    CSATSurvey,
    VoiceCallSession,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── Threads ─────────────────────────────────────────────────────


async def list_threads(
    session: AsyncSession,
    *,
    channel: str | None = None,
    status: str | None = None,
    assigned_agent_id: UUID | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CCThread], int]:
    stmt = select(CCThread)
    if channel is not None:
        stmt = stmt.where(CCThread.channel == channel)
    if status is not None:
        stmt = stmt.where(CCThread.status == status)
    if assigned_agent_id is not None:
        stmt = stmt.where(CCThread.assigned_agent_id == assigned_agent_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                CCThread.subject.ilike(like),
                CCThread.customer_name.ilike(like),
                CCThread.customer_phone.ilike(like),
                CCThread.customer_email.ilike(like),
            ),
        )
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(
                    CCThread.last_message_at.desc().nullslast(),
                    CCThread.created_at.desc(),
                ).limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def get_thread(
    session: AsyncSession, thread_id: UUID, *, for_update: bool = False,
) -> CCThread | None:
    stmt = select(CCThread).where(CCThread.id == thread_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_thread_by_channel_id(
    session: AsyncSession, channel: str, channel_thread_id: str,
) -> CCThread | None:
    stmt = select(CCThread).where(
        CCThread.channel == channel,
        CCThread.channel_thread_id == channel_thread_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_thread(session: AsyncSession, **fields: Any) -> CCThread:
    row = CCThread(**fields)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_thread(
    session: AsyncSession, thread_id: UUID, **fields: Any,
) -> CCThread | None:
    row = await get_thread(session, thread_id, for_update=True)
    if row is None:
        return None
    for k, v in fields.items():
        setattr(row, k, v)
    row.updated_at = _utcnow()
    await session.flush()
    await session.refresh(row)
    return row


async def assign_thread(
    session: AsyncSession, thread_id: UUID, agent_id: UUID,
) -> CCThread | None:
    return await update_thread(
        session, thread_id,
        assigned_agent_id=agent_id,
        status="assigned",
    )


# ─── Messages ────────────────────────────────────────────────────


async def list_messages(
    session: AsyncSession, thread_id: UUID, *, limit: int = 200,
) -> list[CCMessage]:
    stmt = (
        select(CCMessage)
        .where(CCMessage.thread_id == thread_id)
        .order_by(CCMessage.created_at.asc(), CCMessage.id.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def add_message(
    session: AsyncSession,
    *,
    thread_id: UUID,
    direction: str,
    author_kind: str,
    body: str,
    author_user_id: UUID | None = None,
    channel_message_id: str | None = None,
    attachments: list[Any] | None = None,
    ai_meta: dict[str, Any] | None = None,
) -> CCMessage:
    row = CCMessage(
        thread_id=thread_id,
        direction=direction,
        author_kind=author_kind,
        author_user_id=author_user_id,
        body=body,
        channel_message_id=channel_message_id,
        attachments=attachments or [],
        ai_meta=ai_meta,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


# ─── Voice calls ─────────────────────────────────────────────────


async def list_voice_calls(
    session: AsyncSession,
    *,
    status: str | None = None,
    agent_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[VoiceCallSession], int]:
    stmt = select(VoiceCallSession)
    if status is not None:
        stmt = stmt.where(VoiceCallSession.status == status)
    if agent_id is not None:
        stmt = stmt.where(VoiceCallSession.assigned_agent_id == agent_id)
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(VoiceCallSession.started_at.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def get_voice_call(
    session: AsyncSession, call_id: UUID, *, for_update: bool = False,
) -> VoiceCallSession | None:
    stmt = select(VoiceCallSession).where(VoiceCallSession.id == call_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_voice_call_by_channel_id(
    session: AsyncSession, channel_call_id: str,
) -> VoiceCallSession | None:
    """SIM-gateway webhook lookup. `channel_call_id` is UNIQUE on the table."""
    stmt = select(VoiceCallSession).where(
        VoiceCallSession.channel_call_id == channel_call_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_voice_call(
    session: AsyncSession, **fields: Any,
) -> VoiceCallSession:
    row = VoiceCallSession(**fields)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_voice_call(
    session: AsyncSession, call_id: UUID, **fields: Any,
) -> VoiceCallSession | None:
    row = await get_voice_call(session, call_id, for_update=True)
    if row is None:
        return None
    for k, v in fields.items():
        setattr(row, k, v)
    row.updated_at = _utcnow()
    await session.flush()
    await session.refresh(row)
    return row


async def assign_voice_call(
    session: AsyncSession, call_id: UUID, agent_id: UUID,
) -> VoiceCallSession | None:
    return await update_voice_call(
        session, call_id, assigned_agent_id=agent_id,
    )


# ─── CSAT surveys ────────────────────────────────────────────────


async def list_pending_csat(
    session: AsyncSession, *, limit: int = 50, offset: int = 0,
) -> tuple[list[CSATSurvey], int]:
    stmt = select(CSATSurvey).where(CSATSurvey.status.in_(("pending", "sent")))
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(CSATSurvey.created_at.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def list_csat(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CSATSurvey], int]:
    stmt = select(CSATSurvey)
    if status is not None:
        stmt = stmt.where(CSATSurvey.status == status)
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                stmt.order_by(CSATSurvey.created_at.desc())
                .limit(limit).offset(offset),
            )
        ).scalars().all()
    )
    return rows, int(total)


async def get_csat(
    session: AsyncSession, survey_id: UUID, *, for_update: bool = False,
) -> CSATSurvey | None:
    stmt = select(CSATSurvey).where(CSATSurvey.id == survey_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_csat_survey(
    session: AsyncSession, **fields: Any,
) -> CSATSurvey:
    row = CSATSurvey(**fields)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def submit_csat_response(
    session: AsyncSession,
    survey_id: UUID,
    *,
    score: int,
    comment: str | None = None,
) -> CSATSurvey | None:
    row = await get_csat(session, survey_id, for_update=True)
    if row is None:
        return None
    row.score = score
    row.comment = comment
    row.responded_at = _utcnow()
    row.status = "responded"
    await session.flush()
    await session.refresh(row)
    return row
