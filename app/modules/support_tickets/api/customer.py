"""Customer-facing support ticket endpoints.

  GET    /support/tickets                         — list (paged)
  POST   /support/tickets                         — open new
  GET    /support/tickets/{ticket_id}             — detail + messages
  POST   /support/tickets/{ticket_id}/messages    — append a reply

All paths self-scope to the caller's user_id. Looking up another
user's ticket returns 404 (intentional — don't leak existence).
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.support_tickets.schemas import (
    TicketCreateIn,
    TicketDetailOut,
    TicketListOut,
    TicketMessageCreateIn,
    TicketMessageOut,
    TicketOut,
)
from app.modules.support_tickets.service import SupportTicketsService

router = APIRouter(prefix="/support/tickets", tags=["support"])

# Same customer-self gate the wishlist + loyalty endpoints use — the
# customer role grants iam.user.read.self by default. Admin wildcard
# also passes, so support agents can read tickets via this same route
# (with their own user_id; an agent-side admin route is a future build).
_SELF = "iam.user.read.self"


@router.get(
    "",
    response_model=TicketListOut,
    dependencies=[Depends(requires_permission(_SELF))],
    summary="List the caller's support tickets (newest first)",
)
async def list_tickets(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TicketListOut:
    async with uow.transactional() as session:
        svc = SupportTicketsService(session)
        items, total = await svc.list_for_user(
            user_id=principal.user_id, offset=offset, limit=limit,
        )
    return TicketListOut(
        items=[TicketOut.model_validate(t) for t in items],
        total=total,
    )


@router.post(
    "",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_SELF))],
    summary="Open a new support ticket",
)
async def create_ticket(
    payload: TicketCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> TicketOut:
    async with uow.transactional() as session:
        svc = SupportTicketsService(session)
        ticket = await svc.create_ticket(
            user_id=principal.user_id,
            subject=payload.subject,
            body=payload.body,
            category=payload.category,
            priority=payload.priority,
            order_id=payload.order_id,
        )
    return TicketOut.model_validate(ticket)


@router.get(
    "/{ticket_id}",
    response_model=TicketDetailOut,
    dependencies=[Depends(requires_permission(_SELF))],
    summary="Get one of the caller's tickets, with full message thread",
)
async def get_ticket(
    ticket_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> TicketDetailOut:
    async with uow.transactional() as session:
        svc = SupportTicketsService(session)
        ticket = await svc.get_ticket_owned_by(
            ticket_id=ticket_id, user_id=principal.user_id,
        )
        messages = await svc.list_messages(ticket_id=ticket.id)
    return TicketDetailOut(
        **TicketOut.model_validate(ticket).model_dump(),
        messages=[TicketMessageOut.model_validate(m) for m in messages],
    )


@router.post(
    "/{ticket_id}/messages",
    response_model=TicketMessageOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_SELF))],
    summary="Append a customer reply to a ticket thread",
)
async def post_message(
    ticket_id: UUID,
    payload: TicketMessageCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> TicketMessageOut:
    async with uow.transactional() as session:
        svc = SupportTicketsService(session)
        msg = await svc.post_customer_message(
            user_id=principal.user_id,
            ticket_id=ticket_id,
            body=payload.body,
        )
    return TicketMessageOut.model_validate(msg)
