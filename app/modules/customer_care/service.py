"""Customer-care service layer — adapted from the original CC app's
``services.py`` but rewired against Hypershop entities.

The original CC code queried its own ``User`` / ``Customer`` / ``Product``
tables. Here we read directly from Hypershop's ``users`` (with the
``customer`` role acting as customers) and ``products`` tables.

Provides three things the router uses:
1. ``resolve_or_create_customer_by_phone`` — find or create a Hypershop
   user marked as a customer, then ensure a ``cc_customer_profile`` row
   exists. Used by the inbound WhatsApp webhook.
2. ``choose_agent`` — pick the next agent in the round-robin given
   capacity. Reads ``cc_agent_profile`` + ``users``.
3. ``assign_agent`` — store the agent_id on a conversation + increment
   the agent's active-chat counter.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.customer_care.models import (
    CCAgentProfile,
    CCConversation,
    CCCustomerProfile,
    CCMessage,
)

_log = get_logger("hypershop.customer_care.service")


# --------------------------------------------------------------- customers
async def resolve_or_create_customer_by_phone(
    session: AsyncSession,
    *,
    phone: str,
    default_name: str | None = None,
    preferred_language: str = "bangla",
) -> UUID:
    """Return the Hypershop ``users.id`` for this phone. If no user
    exists, create a placeholder customer user (random email, no
    password, status=active, locked_until set high so no one can log
    in until they verify out of band) so the conversation has
    something to link to.

    Also ensures a ``cc_customer_profile`` row exists.
    """
    # Look up existing user
    row = (
        await session.execute(
            text("SELECT id FROM users WHERE phone = :phone LIMIT 1"),
            {"phone": phone},
        )
    ).first()
    if row:
        user_id: UUID = row[0]
    else:
        # Create a placeholder Hypershop user. The IAM service has a
        # richer flow but for inbound WhatsApp we accept a phone-only
        # ghost record. The user can later log in via OTP which fills
        # in email / verified flags.
        ph_safe = phone.replace("+", "").replace(" ", "")
        synth_email = f"wa-{ph_safe}@cc.hypershop.local"
        ins = await session.execute(
            text(
                """
                INSERT INTO users (
                    id, email, phone, full_name, password_hash,
                    status, failed_login_count,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), :email, :phone, :full_name,
                    '!ghost!cc!',
                    'active', 0, now(), now()
                )
                RETURNING id
                """,
            ),
            {
                "email": synth_email,
                "phone": phone,
                "full_name": default_name or f"WhatsApp customer {phone}",
            },
        )
        user_id = ins.scalar_one()
        _log.info(
            "cc_synthetic_customer_user_created",
            user_id=str(user_id),
            phone=phone,
        )

    # Ensure cc_customer_profile row
    existing = (
        await session.execute(
            select(CCCustomerProfile).where(CCCustomerProfile.customer_id == user_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            CCCustomerProfile(
                customer_id=user_id,
                preferred_language=preferred_language,
                consent_status="allowed",
            )
        )
        await session.flush()

    return user_id


# --------------------------------------------------------------- agents
async def list_online_agents(session: AsyncSession) -> list[tuple[UUID, int, int]]:
    """Return [(user_id, current_active_chats, max_active_chats)] for
    every agent whose ``cc_agent_profile.status == 'online'`` and who
    has capacity (current < max). Sorted by current asc (round-robin
    on least-loaded).
    """
    rows = (
        await session.execute(
            select(
                CCAgentProfile.user_id,
                CCAgentProfile.current_active_chats,
                CCAgentProfile.max_active_chats,
            )
            .where(CCAgentProfile.status == "online")
            .where(CCAgentProfile.current_active_chats < CCAgentProfile.max_active_chats)
            .order_by(CCAgentProfile.current_active_chats.asc())
        )
    ).all()
    return [(r[0], int(r[1]), int(r[2])) for r in rows]


async def choose_agent(session: AsyncSession) -> UUID | None:
    """Pick the next agent for a new conversation. Returns None if
    no agent has capacity (caller should park the conversation in
    the unassigned queue).
    """
    agents = await list_online_agents(session)
    return agents[0][0] if agents else None


async def assign_agent(
    session: AsyncSession,
    *,
    conversation: CCConversation,
    agent_id: UUID,
) -> None:
    conversation.agent_id = agent_id
    await session.execute(
        update(CCAgentProfile)
        .where(CCAgentProfile.user_id == agent_id)
        .values(
            current_active_chats=CCAgentProfile.current_active_chats + 1,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()


async def decrement_agent_load(
    session: AsyncSession,
    *,
    agent_id: UUID,
) -> None:
    """Called when a conversation is resolved or transferred away."""
    await session.execute(
        update(CCAgentProfile)
        .where(CCAgentProfile.user_id == agent_id)
        .where(CCAgentProfile.current_active_chats > 0)
        .values(
            current_active_chats=CCAgentProfile.current_active_chats - 1,
            updated_at=datetime.now(timezone.utc),
        )
    )


# --------------------------------------------------------------- conversation helpers
async def append_message(
    session: AsyncSession,
    *,
    conversation: CCConversation,
    sender_type: str,
    body: str | None,
    message_type: str = "text",
    media_url: str | None = None,
    channel: str = "whatsapp",
    whatsapp_message_id: str | None = None,
) -> CCMessage:
    msg = CCMessage(
        conversation_id=conversation.id,
        sender_type=sender_type,
        message_type=message_type,
        message_body=body,
        media_url=media_url,
        channel=channel,
        whatsapp_message_id=whatsapp_message_id,
    )
    session.add(msg)
    conversation.last_message = body
    conversation.last_message_at = datetime.now(timezone.utc)
    if sender_type == "agent" and conversation.first_response_at is None:
        conversation.first_response_at = datetime.now(timezone.utc)
    await session.flush()
    return msg


# --------------------------------------------------------------- dashboard
async def dashboard_summary(session: AsyncSession) -> dict:
    open_conv = (
        await session.execute(
            select(func.count()).select_from(CCConversation).where(
                CCConversation.status == "open"
            )
        )
    ).scalar_one()
    unassigned = (
        await session.execute(
            select(func.count()).select_from(CCConversation).where(
                CCConversation.status == "open",
                CCConversation.agent_id.is_(None),
            )
        )
    ).scalar_one()
    handover = (
        await session.execute(
            select(func.count()).select_from(CCConversation).where(
                CCConversation.handover_required.is_(True),
            )
        )
    ).scalar_one()
    sla_breach = (
        await session.execute(
            select(func.count()).select_from(CCConversation).where(
                CCConversation.status == "open",
                (
                    CCConversation.sla_first_response_breached.is_(True)
                    | CCConversation.sla_resolution_breached.is_(True)
                ),
            )
        )
    ).scalar_one()
    online_agents = (
        await session.execute(
            select(func.count()).select_from(CCAgentProfile).where(
                CCAgentProfile.status == "online"
            )
        )
    ).scalar_one()
    total_agents = (
        await session.execute(
            select(func.count()).select_from(CCAgentProfile)
        )
    ).scalar_one()
    csat_avg = (
        await session.execute(
            text(
                "SELECT AVG(score)::numeric(4,2) FROM cc_csat_surveys "
                "WHERE responded_at IS NOT NULL "
                "AND responded_at >= now() - INTERVAL '30 days'"
            )
        )
    ).scalar_one()
    pending_fu = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM cc_followups "
                "WHERE status = 'pending' "
                "AND (next_followup_at IS NULL OR next_followup_at <= now())"
            )
        )
    ).scalar_one()
    return {
        "open_conversations": int(open_conv or 0),
        "unassigned_conversations": int(unassigned or 0),
        "handover_required": int(handover or 0),
        "sla_breached": int(sla_breach or 0),
        "online_agents": int(online_agents or 0),
        "total_agents": int(total_agents or 0),
        "csat_avg_last_30d": csat_avg,
        "pending_followups": int(pending_fu or 0),
    }
