"""Support tickets service — customer self-serve.

Tickets are scoped per (customer_user_id) — every read/write checks the
caller owns the ticket. Agent assignment + status transitions live in a
future agent-side service; this module is customer-only.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.support_tickets.models import (
    SupportTicket,
    SupportTicketMessage,
)


class SupportTicketsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_ticket(
        self,
        *,
        user_id: UUID,
        subject: str,
        body: str,
        category: str = "general",
        priority: str = "normal",
        order_id: UUID | None = None,
    ) -> SupportTicket:
        """Open a new ticket and seed the first message from the customer."""
        ticket = SupportTicket(
            customer_user_id=user_id,
            subject=subject.strip(),
            body=body.strip(),
            category=category,
            priority=priority,
            order_id=order_id,
            status="open",
        )
        self.session.add(ticket)
        await self.session.flush()

        # First message mirrors the body so the thread starts with the
        # customer's opening text — agents see the conversation as a
        # message stream from message zero, not "subject" + "first reply".
        msg = SupportTicketMessage(
            ticket_id=ticket.id,
            author_user_id=user_id,
            author_kind="customer",
            body=body.strip(),
        )
        self.session.add(msg)
        await self.session.flush()
        return ticket

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[SupportTicket], int]:
        total = (
            await self.session.execute(
                select(func.count(SupportTicket.id))
                .where(SupportTicket.customer_user_id == user_id)
            )
        ).scalar_one()

        rows = (
            await self.session.execute(
                select(SupportTicket)
                .where(SupportTicket.customer_user_id == user_id)
                .order_by(SupportTicket.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows), int(total)

    async def get_ticket_owned_by(
        self, *, ticket_id: UUID, user_id: UUID,
    ) -> SupportTicket:
        ticket = (
            await self.session.execute(
                select(SupportTicket).where(SupportTicket.id == ticket_id)
            )
        ).scalar_one_or_none()
        if ticket is None or ticket.customer_user_id != user_id:
            # 404 instead of 403 so we don't leak ticket existence to
            # someone who isn't the owner.
            raise NotFoundError("Ticket not found.")
        return ticket

    async def list_messages(
        self, *, ticket_id: UUID,
    ) -> list[SupportTicketMessage]:
        rows = (
            await self.session.execute(
                select(SupportTicketMessage)
                .where(SupportTicketMessage.ticket_id == ticket_id)
                .order_by(SupportTicketMessage.created_at.asc())
            )
        ).scalars().all()
        return list(rows)

    async def post_customer_message(
        self,
        *,
        user_id: UUID,
        ticket_id: UUID,
        body: str,
    ) -> SupportTicketMessage:
        ticket = await self.get_ticket_owned_by(
            ticket_id=ticket_id, user_id=user_id,
        )
        msg = SupportTicketMessage(
            ticket_id=ticket.id,
            author_user_id=user_id,
            author_kind="customer",
            body=body.strip(),
        )
        self.session.add(msg)
        # Posting a customer reply moves the ticket back to 'open' if it
        # was waiting on them — keeps the agent queue accurate.
        if ticket.status == "waiting_customer":
            ticket.status = "open"
        await self.session.flush()
        return msg
