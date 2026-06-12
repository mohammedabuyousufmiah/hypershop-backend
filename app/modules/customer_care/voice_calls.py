"""Voice-call service — state-machine + outbox emission.

State transitions enforced here:

    ringing  → assigned   (assign_to_agent)
    ringing  → missed     (mark_missed)
    assigned → in_call    (mark_answered)
    assigned → ringing    (release_back_to_queue, e.g. agent rejected)
    in_call  → ended      (mark_ended)
    ringing  → ended      (mark_ended  — caller hung up before pickup)

Every transition appends a row to ``cc_voice_call_events`` and enqueues
exactly one outbox message under ``voice.call.<verb>`` so the
softphone/SSE worker can react.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, NotFoundError
from app.core.events.outbox import enqueue_outbox
from app.modules.customer_care.models import CCVoiceCall, CCVoiceCallEvent

# Valid state transitions. (from, to) → event_type emitted.
# Note: ringing → in_call exists for telephony providers that bridge
# the call to an IVR/agent directly without going through our explicit
# assign step (e.g. Banglalink HUB auto-routing on a hunt group).
_ALLOWED: dict[tuple[str, str], str] = {
    ("ringing", "assigned"):  "voice.call.assigned",
    ("ringing", "in_call"):   "voice.call.answered",
    ("ringing", "missed"):    "voice.call.missed",
    ("ringing", "ended"):     "voice.call.ended",
    ("assigned", "in_call"):  "voice.call.answered",
    ("assigned", "ringing"):  "voice.call.released",
    ("assigned", "ended"):    "voice.call.ended",
    ("in_call", "ended"):     "voice.call.ended",
}


async def _load_call(session: AsyncSession, call_id: UUID) -> CCVoiceCall:
    call = (
        await session.execute(select(CCVoiceCall).where(CCVoiceCall.id == call_id))
    ).scalar_one_or_none()
    if call is None:
        raise NotFoundError("Voice call not found")
    return call


async def _transition(
    session: AsyncSession,
    *,
    call: CCVoiceCall,
    to_status: str,
    actor_id: UUID | None,
    event_payload: dict[str, Any] | None = None,
) -> CCVoiceCall:
    key = (call.status, to_status)
    if key not in _ALLOWED:
        raise BusinessRuleError(
            f"Illegal voice-call transition: {call.status} -> {to_status}"
        )
    event_type = _ALLOWED[key]
    from_status = call.status
    call.status = to_status
    now = datetime.now(timezone.utc)
    if to_status == "assigned":
        call.assigned_at = now
    elif to_status == "in_call":
        call.answered_at = now
    elif to_status in ("ended", "missed"):
        call.ended_at = now
        if call.answered_at is not None:
            call.duration_seconds = int(
                (now - call.answered_at).total_seconds()
            )
    session.add(
        CCVoiceCallEvent(
            voice_call_id=call.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            actor_id=actor_id,
            payload=event_payload or {},
        )
    )
    await session.flush()
    await enqueue_outbox(
        type=event_type,
        payload={
            "voice_call_id": str(call.id),
            "provider": call.provider,
            "provider_call_id": call.provider_call_id,
            "from_phone": call.from_phone,
            "customer_id": str(call.customer_id) if call.customer_id else None,
            "agent_id": str(call.agent_id) if call.agent_id else None,
            "from_status": from_status,
            "to_status": to_status,
        },
        metadata={"actor_id": str(actor_id) if actor_id else None},
        session=session,
    )
    return call


# ---------------------------------------------------------------- public API
async def ingest_inbound(
    session: AsyncSession,
    *,
    provider: str,
    provider_call_id: str,
    from_phone: str,
    to_number: str | None = None,
    customer_id: UUID | None = None,
    priority: str = "normal",
    extra: dict[str, Any] | None = None,
) -> CCVoiceCall:
    """Idempotent: if (provider, provider_call_id) already exists, returns it."""
    existing = (
        await session.execute(
            select(CCVoiceCall).where(
                CCVoiceCall.provider == provider,
                CCVoiceCall.provider_call_id == provider_call_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    call = CCVoiceCall(
        provider=provider,
        provider_call_id=provider_call_id,
        from_phone=from_phone,
        to_number=to_number,
        customer_id=customer_id,
        priority=priority,
        status="ringing",
        metadata_=extra or {},
    )
    session.add(call)
    await session.flush()
    session.add(
        CCVoiceCallEvent(
            voice_call_id=call.id,
            event_type="voice.call.ringing",
            from_status=None,
            to_status="ringing",
            payload={"provider": provider, "from_phone": from_phone},
        )
    )
    await enqueue_outbox(
        type="voice.call.ringing",
        payload={
            "voice_call_id": str(call.id),
            "provider": provider,
            "provider_call_id": provider_call_id,
            "from_phone": from_phone,
            "priority": priority,
        },
        session=session,
    )
    return call


async def assign_to_agent(
    session: AsyncSession,
    *,
    call_id: UUID,
    agent_id: UUID,
    actor_id: UUID,
) -> CCVoiceCall:
    """Route a ringing call to a specific agent.

    Raises BusinessRuleError if the call is not in ``ringing`` state
    (already assigned, in-call, ended, or missed).
    """
    call = await _load_call(session, call_id)
    call.agent_id = agent_id
    return await _transition(
        session,
        call=call,
        to_status="assigned",
        actor_id=actor_id,
        event_payload={"target_agent_id": str(agent_id)},
    )


async def dispatch_inbound(
    session: AsyncSession,
    *,
    call: CCVoiceCall,
    actor_id: UUID,
) -> dict[str, Any]:
    """ACD: route a freshly-ingested ringing call to a free agent.

    Free agent = cc_agent_profile.status='available', has a SIP extension,
    and is NOT already on an active call (assigned/in_call). Picks the
    least-loaded one. If none free → leave the call ringing+queued and
    return a wait message + queue position for the caller IVR.
    """
    # Free agent = available + has a LOCAL PHONE (SIM-gateway dials it) and
    # not already on an active call. SIP extension optional (local-call mode).
    free = (
        await session.execute(
            text(
                """
                SELECT a.user_id::text AS user_id, a.sip_extension,
                       u.phone AS agent_phone
                FROM cc_agent_profile a
                JOIN users u ON u.id = a.user_id
                WHERE a.status = 'available'
                  AND u.phone IS NOT NULL
                  AND a.user_id NOT IN (
                      SELECT agent_id FROM cc_voice_calls
                      WHERE agent_id IS NOT NULL
                        AND status IN ('assigned', 'in_call')
                  )
                ORDER BY a.current_active_chats ASC, a.updated_at ASC
                LIMIT 1
                """
            )
        )
    ).mappings().first()

    if free is not None:
        await assign_to_agent(
            session, call_id=call.id, agent_id=UUID(free["user_id"]), actor_id=actor_id,
        )
        # Local-SIM click-to-call: ring the agent's phone, bridge to caller.
        call_placed = False
        try:
            from app.modules.customer_care.channels import place_voice_call

            resp = await place_voice_call(
                to_phone=free["agent_phone"],
                message=f"Incoming customer call from {call.from_phone}.",
                bridge_to=call.from_phone,
            )
            call_placed = resp is not None
        except Exception:  # noqa: BLE001 — never fail the dispatch on gateway error
            call_placed = False
        return {
            "dispatched": True,
            "queued": False,
            "agent_id": free["user_id"],
            "agent_phone": free["agent_phone"],
            "extension": free["sip_extension"],
            "call_placed": call_placed,
            "mode": "local_sim_bridge",
        }

    # No free agent → keep ringing, mark queued, tell caller to wait.
    position = (
        await session.execute(
            text(
                "SELECT count(*) FROM cc_voice_calls "
                "WHERE status = 'ringing' AND agent_id IS NULL"
            )
        )
    ).scalar() or 1
    md = dict(call.metadata_ or {})
    md["queued"] = True
    md["queue_position"] = int(position)
    call.metadata_ = md
    session.add(
        CCVoiceCallEvent(
            voice_call_id=call.id,
            event_type="voice.call.queued",
            from_status="ringing",
            to_status="ringing",
            payload={"queue_position": int(position)},
        )
    )
    await enqueue_outbox(
        type="voice.call.queued",
        payload={"voice_call_id": str(call.id), "queue_position": int(position)},
        session=session,
    )
    return {
        "dispatched": False,
        "queued": True,
        "position": int(position),
        "wait_message": (
            f"All agents are busy. You are number {int(position)} in the "
            "queue — please hold and the next available agent will take your call."
        ),
    }


async def release_back_to_queue(
    session: AsyncSession, *, call_id: UUID, actor_id: UUID, reason: str | None = None,
) -> CCVoiceCall:
    call = await _load_call(session, call_id)
    call.agent_id = None
    return await _transition(
        session,
        call=call,
        to_status="ringing",
        actor_id=actor_id,
        event_payload={"reason": reason},
    )


async def mark_answered(
    session: AsyncSession, *, call_id: UUID, actor_id: UUID | None = None,
) -> CCVoiceCall:
    """Webhook-friendly: actor_id is None when fired by the SBC."""
    return await _transition(
        session, call=await _load_call(session, call_id),
        to_status="in_call", actor_id=actor_id,
    )


async def drain_queue(
    session: AsyncSession, *, actor_id: UUID | None = None, max_pull: int = 20,
) -> list[str]:
    """An agent just freed up → pull the oldest queued (ringing+unassigned)
    calls and dispatch them, until no free agent remains. Returns the call
    ids that got assigned."""
    drained: list[str] = []
    for _ in range(max_pull):
        nxt = (
            await session.execute(
                text(
                    "SELECT id::text FROM cc_voice_calls "
                    "WHERE status = 'ringing' AND agent_id IS NULL "
                    "ORDER BY started_at ASC LIMIT 1"
                )
            )
        ).scalar()
        if not nxt:
            break
        call = await _load_call(session, UUID(nxt))
        res = await dispatch_inbound(session, call=call, actor_id=actor_id)
        if not res.get("dispatched"):
            break  # no free agent → stop draining
        drained.append(nxt)
    return drained


async def mark_ended(
    session: AsyncSession, *, call_id: UUID, actor_id: UUID | None,
    recording_url: str | None = None,
) -> CCVoiceCall:
    call = await _load_call(session, call_id)
    if recording_url:
        call.recording_url = recording_url
    result = await _transition(
        session, call=call, to_status="ended", actor_id=actor_id,
        event_payload={"recording_url": recording_url} if recording_url else None,
    )
    # Agent freed → auto-pull next queued caller.
    await drain_queue(session, actor_id=actor_id)
    return result


async def mark_missed(
    session: AsyncSession, *, call_id: UUID,
) -> CCVoiceCall:
    result = await _transition(
        session, call=await _load_call(session, call_id),
        to_status="missed", actor_id=None,
    )
    await drain_queue(session)
    return result
