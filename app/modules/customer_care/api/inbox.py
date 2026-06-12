"""Admin endpoints — unified WhatsApp / email inbox."""
from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.customer_care import cc_inbox_repository as repo
from app.modules.customer_care import cc_inbox_service as svc

router = APIRouter(prefix="/admin/cc/inbox", tags=["admin-cc-inbox"])

_PERM_VIEW = "cc_inbox.view"
_PERM_REPLY = "cc_inbox.reply"
_PERM_ADMIN = "cc_inbox.admin"


def _thread_dict(t: Any) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "channel": t.channel,
        "channel_thread_id": t.channel_thread_id,
        "customer_user_id": str(t.customer_user_id) if t.customer_user_id else None,
        "customer_phone": t.customer_phone,
        "customer_email": t.customer_email,
        "customer_name": t.customer_name,
        "subject": t.subject,
        "status": t.status,
        "assigned_agent_id": str(t.assigned_agent_id) if t.assigned_agent_id else None,
        "priority": t.priority,
        "ai_confidence": str(t.ai_confidence) if t.ai_confidence is not None else None,
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        "last_inbound_at": t.last_inbound_at.isoformat() if t.last_inbound_at else None,
        "last_agent_response_at": (
            t.last_agent_response_at.isoformat() if t.last_agent_response_at else None
        ),
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "tags": t.tags or [],
        "created_at": t.created_at.isoformat(),
    }


def _msg_dict(m: Any) -> dict[str, Any]:
    return {
        "id": int(m.id),
        "thread_id": str(m.thread_id),
        "direction": m.direction,
        "author_kind": m.author_kind,
        "author_user_id": str(m.author_user_id) if m.author_user_id else None,
        "body": m.body,
        "channel_message_id": m.channel_message_id,
        "attachments": m.attachments or [],
        "ai_meta": m.ai_meta,
        "created_at": m.created_at.isoformat(),
    }


class AssignBody(BaseModel):
    agent_user_id: UUID


class ReplyBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    attachments: list[dict[str, Any]] | None = None


class ResolveBody(BaseModel):
    resolution_note: str | None = None


class CloseBody(BaseModel):
    mark_spam: bool = False


@router.get(
    "/threads",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List threads with filters",
)
async def list_threads(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    channel: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    assigned_agent_id: Annotated[UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows, total = await repo.list_threads(
            session,
            channel=channel, status=status_filter,
            assigned_agent_id=assigned_agent_id, q=q,
            limit=limit, offset=offset,
        )
    return {
        "items": [_thread_dict(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get(
    "/threads/{thread_id}",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Read a thread",
)
async def get_thread(
    thread_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        t = await repo.get_thread(session, thread_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Thread not found.")
    return _thread_dict(t)


@router.get(
    "/threads/{thread_id}/messages",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List messages in a thread",
)
async def list_thread_messages(
    thread_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows = await repo.list_messages(session, thread_id, limit=limit)
    return {"items": [_msg_dict(r) for r in rows], "total": len(rows)}


@router.post(
    "/threads/{thread_id}/messages",
    dependencies=[Depends(requires_permission(_PERM_REPLY))],
    status_code=status.HTTP_201_CREATED,
    summary="Agent reply",
)
async def agent_reply(
    thread_id: UUID,
    body: ReplyBody,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        try:
            msg = await svc.agent_reply(
                session,
                thread_id=thread_id,
                agent_user_id=principal.user_id,
                body=body.body,
                attachments=body.attachments,
            )
        except svc.ThreadNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return _msg_dict(msg)


@router.post(
    "/threads/{thread_id}/assign",
    dependencies=[Depends(requires_permission(_PERM_ADMIN))],
    summary="Assign thread to an agent",
)
async def assign_thread(
    thread_id: UUID,
    body: AssignBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        try:
            t = await svc.assign_thread(
                session, thread_id=thread_id, agent_user_id=body.agent_user_id,
            )
        except svc.ThreadNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return _thread_dict(t)


@router.post(
    "/threads/{thread_id}/resolve",
    dependencies=[Depends(requires_permission(_PERM_REPLY))],
    summary="Resolve a thread + send CSAT",
)
async def resolve_thread(
    thread_id: UUID,
    body: Annotated[ResolveBody, Body()],
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        try:
            t = await svc.resolve_thread(
                session, thread_id=thread_id, by_user_id=principal.user_id,
            )
        except svc.ThreadNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return _thread_dict(t)


@router.post(
    "/threads/{thread_id}/close",
    dependencies=[Depends(requires_permission(_PERM_ADMIN))],
    summary="Close a thread (optionally as spam)",
)
async def close_thread(
    thread_id: UUID,
    body: Annotated[CloseBody, Body()],
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        try:
            t = await svc.close_thread(
                session,
                thread_id=thread_id,
                by_user_id=principal.user_id,
                mark_spam=body.mark_spam,
            )
        except svc.ThreadNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return _thread_dict(t)


@router.post(
    "/threads/{thread_id}/ai-draft",
    dependencies=[Depends(requires_permission(_PERM_REPLY))],
    summary="AI-draft a reply (auto-sends if confidence > 0.85)",
)
async def ai_draft(
    thread_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        try:
            return await svc.try_ai_auto_reply(session, thread_id=thread_id)
        except svc.ThreadNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
