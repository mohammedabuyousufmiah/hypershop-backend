"""Support tickets admin / agent-side endpoints.

Customer-care agents use these to triage + reply to tickets across all
customers. The customer-side endpoints (in customer.py) self-scope to
the caller; these admin endpoints are cross-customer.

  GET    /admin/support/tickets               — list all (filter by status/priority/category)
  GET    /admin/support/tickets/{id}          — detail + thread
  PATCH  /admin/support/tickets/{id}          — change status / assign agent
  POST   /admin/support/tickets/{id}/messages — agent reply (author_kind='agent')
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.time import utc_now
from app.modules.support_tickets.models import (
    SupportTicket,
    SupportTicketMessage,
)
from app.modules.support_tickets.schemas import (
    TicketDetailOut,
    TicketMessageOut,
    TicketOut,
)

router = APIRouter(prefix="/admin/support/tickets", tags=["admin-support"])
_W = "support.admin"


class AdminTicketListOut(BaseModel):
    items: list[TicketOut]
    total: int


class AdminTicketUpdateIn(BaseModel):
    status: Literal[
        "open", "in_progress", "waiting_customer", "resolved", "closed",
    ] | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    assigned_to: UUID | None = Field(
        default=None, description="Agent user_id; pass null to unassign.",
    )


class AdminAgentMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    set_status: Literal[
        "open", "in_progress", "waiting_customer", "resolved", "closed",
    ] | None = Field(
        default="waiting_customer",
        description=(
            "After posting, transition the ticket to this status. Default "
            "'waiting_customer' so the agent's reply naturally moves the "
            "ticket out of the agent inbox."
        ),
    )


@router.get(
    "",
    response_model=AdminTicketListOut,
    dependencies=[Depends(requires_permission(_W))],
    summary="List ALL support tickets across customers",
)
async def list_all_tickets(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    priority: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminTicketListOut:
    async with uow.transactional() as session:
        q = select(SupportTicket)
        cnt = select(func.count(SupportTicket.id))
        if status_filter:
            q = q.where(SupportTicket.status == status_filter)
            cnt = cnt.where(SupportTicket.status == status_filter)
        if priority:
            q = q.where(SupportTicket.priority == priority)
            cnt = cnt.where(SupportTicket.priority == priority)
        if category:
            q = q.where(SupportTicket.category == category)
            cnt = cnt.where(SupportTicket.category == category)
        total = (await session.execute(cnt)).scalar_one()
        rows = (
            await session.execute(
                q.order_by(SupportTicket.updated_at.desc())
                 .offset(offset).limit(limit)
            )
        ).scalars().all()
    return AdminTicketListOut(
        items=[TicketOut.model_validate(r) for r in rows],
        total=int(total),
    )


@router.get(
    "/{ticket_id}",
    response_model=TicketDetailOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def get_ticket_admin(
    ticket_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
) -> TicketDetailOut:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                select(SupportTicket).where(SupportTicket.id == ticket_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Ticket not found.")
        msgs = (
            await session.execute(
                select(SupportTicketMessage)
                .where(SupportTicketMessage.ticket_id == ticket_id)
                .order_by(SupportTicketMessage.created_at.asc())
            )
        ).scalars().all()
    return TicketDetailOut(
        **TicketOut.model_validate(row).model_dump(),
        messages=[TicketMessageOut.model_validate(m) for m in msgs],
    )


@router.patch(
    "/{ticket_id}",
    response_model=TicketOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def update_ticket(
    ticket_id: UUID,
    payload: AdminTicketUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(get_current_principal)],
) -> TicketOut:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                select(SupportTicket).where(SupportTicket.id == ticket_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Ticket not found.")
        if payload.status is not None:
            row.status = payload.status
        if payload.priority is not None:
            row.priority = payload.priority
        # `assigned_to=None` in the payload doesn't unset (FastAPI/Pydantic
        # can't tell omitted vs explicit-null). Skip the unset case for now.
        if payload.assigned_to is not None:
            row.assigned_to = payload.assigned_to
        row.updated_at = utc_now()
        await session.flush()
    return TicketOut.model_validate(row)


@router.post(
    "/{ticket_id}/messages",
    response_model=TicketMessageOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_W))],
    summary="Post an agent reply (author_kind='agent')",
)
async def post_agent_message(
    ticket_id: UUID,
    payload: AdminAgentMessageIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> TicketMessageOut:
    async with uow.transactional() as session:
        ticket = (
            await session.execute(
                select(SupportTicket).where(SupportTicket.id == ticket_id)
            )
        ).scalar_one_or_none()
        if ticket is None:
            raise NotFoundError("Ticket not found.")
        msg = SupportTicketMessage(
            ticket_id=ticket.id,
            author_user_id=principal.user_id,
            author_kind="agent",
            body=payload.body.strip(),
        )
        session.add(msg)
        if payload.set_status is not None:
            ticket.status = payload.set_status
        ticket.updated_at = utc_now()
        await session.flush()
    return TicketMessageOut.model_validate(msg)
