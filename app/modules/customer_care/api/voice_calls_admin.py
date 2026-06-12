"""Admin endpoints — voice call sessions."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.customer_care import cc_inbox_repository as repo
from app.modules.customer_care import cc_inbox_service as svc

router = APIRouter(prefix="/admin/cc/voice-calls", tags=["admin-cc-voice"])

_PERM_VIEW = "voice_call.view"
_PERM_ASSIGN = "voice_call.assign"


def _call_dict(c: Any) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "channel_call_id": c.channel_call_id,
        "direction": c.direction,
        "caller_phone": c.caller_phone,
        "callee_phone": c.callee_phone,
        "customer_user_id": str(c.customer_user_id) if c.customer_user_id else None,
        "assigned_agent_id": str(c.assigned_agent_id) if c.assigned_agent_id else None,
        "status": c.status,
        "started_at": c.started_at.isoformat(),
        "answered_at": c.answered_at.isoformat() if c.answered_at else None,
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        "duration_seconds": c.duration_seconds,
        "recording_url": c.recording_url,
        "transcript_lang": c.transcript_lang,
        "summary": c.summary,
        "next_action": c.next_action,
        "next_action_at": c.next_action_at.isoformat() if c.next_action_at else None,
        "csat_score": c.csat_score,
        "created_at": c.created_at.isoformat(),
    }


class AssignBody(BaseModel):
    agent_user_id: UUID


class NoteBody(BaseModel):
    note: str = Field(min_length=1, max_length=4000)
    next_action: str | None = None
    next_action_at: datetime | None = None


@router.get(
    "",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List voice calls",
)
async def list_calls(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    assigned_agent_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows, total = await repo.list_voice_calls(
            session,
            status=status_filter, agent_id=assigned_agent_id,
            limit=limit, offset=offset,
        )
    return {
        "items": [_call_dict(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get(
    "/{call_id}",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Read a voice call",
)
async def get_call(
    call_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        c = await repo.get_voice_call(session, call_id)
        if c is None:
            raise HTTPException(status_code=404, detail="Voice call not found.")
    return _call_dict(c)


@router.get(
    "/{call_id}/transcript",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Full transcript text",
)
async def get_transcript(
    call_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        c = await repo.get_voice_call(session, call_id)
        if c is None:
            raise HTTPException(status_code=404, detail="Voice call not found.")
    return {
        "id": str(c.id),
        "transcript": c.transcript or "",
        "lang": c.transcript_lang,
    }


@router.post(
    "/{call_id}/assign",
    dependencies=[Depends(requires_permission(_PERM_ASSIGN))],
    summary="Assign call to agent",
)
async def assign_call(
    call_id: UUID,
    body: AssignBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        try:
            c = await svc.assign_voice_call(
                session, call_id=call_id, agent_user_id=body.agent_user_id,
            )
        except svc.VoiceCallNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return _call_dict(c)


@router.post(
    "/{call_id}/note",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Append a wrap-up note + optional next action",
)
async def append_note(
    call_id: UUID,
    body: Annotated[NoteBody, Body()],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        try:
            c = await svc.append_voice_note(
                session,
                call_id=call_id,
                note=body.note,
                next_action=body.next_action,
                next_action_at=body.next_action_at,
            )
        except svc.VoiceCallNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return _call_dict(c)
