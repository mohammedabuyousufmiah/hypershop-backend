"""Customer-care HTTP API.

Mounted under ``/api/v1/customer-care/*``. All routes require an
authenticated Hypershop user with one of:
- ``customercare.agent``   — basic inbox + reply
- ``customercare.admin``   — agent mgmt, follow-ups, SLA
- ``customercare.rag.admin`` — knowledge-base operations

Public sub-routes (no auth) are at ``/api/v1/customer-care/webhooks/*``
for inbound WhatsApp + storefront + payment provider hooks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request, status
from sqlalchemy import desc, select, text as _sa_text
from sqlalchemy.orm import selectinload

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import BusinessRuleError, NotFoundError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.audit.service import record_audit
from app.modules.customer_care import outbound, service, sse_bus
from app.modules.customer_care.models import (
    CCAgentProfile,
    CCConversation,
    CCCustomerProfile,
    CCFollowup,
    CCMessage,
    CCWebhookIdempotency,
)
from app.modules.customer_care.schemas import (
    AgentProfileResponse,
    AgentStatusUpdate,
    ConversationDetail,
    ConversationSummary,
    CustomerProfileResponse,
    DashboardSummary,
    FollowupCreate,
    FollowupResponse,
    MessageResponse,
    ResolveConversationRequest,
    SendMessageRequest,
    TransferConversationRequest,
)

_AGENT = "customercare.agent"
_ADMIN = "customercare.admin"
_RAG_ADMIN = "customercare.rag.admin"

# Short-form perms added 2026-05-16 for the new triage + voice-call
# endpoints. Distinct from `customercare.agent` because:
#   - `ai_care.view` is held by ai_manager (audit AI proposals) in
#     addition to the CC agent/admin roles — it's NOT a write verb.
#   - `voice_call.assign` is held by support_agent + customercare_admin
#     but NOT by the basic customercare_agent (routing decisions are a
#     supervisor task in this org).
_AI_CARE_VIEW = "ai_care.view"
_VOICE_CALL_ASSIGN = "voice_call.assign"

router = APIRouter(prefix="/customer-care", tags=["customer-care"])


# ---------------------------------------------------------------- helpers
async def _load_conversation(
    session, conv_id: UUID, *, include_messages: bool = False
) -> CCConversation:
    stmt = select(CCConversation).where(CCConversation.id == conv_id)
    if include_messages:
        stmt = stmt.options(selectinload(CCConversation.__mapper__.relationships))
    conv = (await session.execute(stmt)).scalar_one_or_none()
    if conv is None:
        raise NotFoundError("Conversation not found")
    return conv


async def _try_rag_context(uow: UnitOfWork, query: str, k: int = 3) -> str | None:
    """Best-effort: fetch top-k KB chunks for the query and return a
    concatenated context string under ~2400 chars. Silently returns
    ``None`` on any failure (no embeddings, no chunks, network error)
    so the caller can degrade to a no-context AI reply.
    """
    if not query.strip():
        return None
    try:
        embs = await outbound.embed_texts([query])
        if not embs or not embs[0]:
            return None
        from sqlalchemy import text as _t
        async with uow.transactional() as s:
            rows = (
                await s.execute(
                    _t(
                        "SELECT c.text, c.embedding "
                        "FROM cc_knowledge_chunks c "
                        "JOIN cc_knowledge_documents d ON d.id = c.document_id "
                        "WHERE d.is_active = true AND c.embedding IS NOT NULL "
                        "LIMIT 5000"
                    ),
                )
            ).all()
        if not rows:
            return None
        import json as _j
        q = embs[0]
        scored = []
        for r in rows:
            try:
                v = _j.loads(r[1])
            except Exception:  # noqa: BLE001
                continue
            scored.append((outbound.cosine_similarity(q, v), r[0]))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [t for sc, t in scored[:k] if sc > 0.35]
        if not top:
            return None
        ctx = "\n---\n".join(top)
        return ctx[:2400]
    except Exception:  # noqa: BLE001
        return None


async def _load_customer_summary(session, customer_id: UUID) -> dict:
    """Return a tiny customer summary: (full_name, phone, language)."""
    from sqlalchemy import text as _text
    row = (
        await session.execute(
            _text(
                "SELECT u.full_name, u.phone, p.preferred_language, "
                "p.consent_status "
                "FROM users u "
                "LEFT JOIN cc_customer_profile p ON p.customer_id = u.id "
                "WHERE u.id = :cid"
            ),
            {"cid": customer_id},
        )
    ).first()
    if row is None:
        return {"full_name": None, "phone": None, "preferred_language": None, "consent_status": None}
    return {
        "full_name": row[0],
        "phone": row[1],
        "preferred_language": row[2],
        "consent_status": row[3],
    }


# ---------------------------------------------------------------- /me + dashboard
@router.get(
    "/me",
    response_model=AgentProfileResponse,
    summary="Get the current agent's CC profile (creates a row on first call)",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def get_my_profile(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AgentProfileResponse:
    async with uow.transactional() as session:
        prof = (
            await session.execute(
                select(CCAgentProfile).where(CCAgentProfile.user_id == principal.user_id)
            )
        ).scalar_one_or_none()
        if prof is None:
            # Auto-provision on first dashboard-load
            prof = CCAgentProfile(user_id=principal.user_id, status="offline")
            session.add(prof)
            await session.flush()
        from sqlalchemy import text as _t
        u = (
            await session.execute(
                _t("SELECT email::text, full_name FROM users WHERE id = :uid"),
                {"uid": principal.user_id},
            )
        ).first()
        return AgentProfileResponse(
            user_id=prof.user_id,
            email=u[0] if u else None,
            full_name=u[1] if u else None,
            status=prof.status,
            max_active_chats=prof.max_active_chats,
            current_active_chats=prof.current_active_chats,
            language_skill=prof.language_skill,
        )


@router.patch(
    "/me/status",
    response_model=AgentProfileResponse,
    summary="Update the current agent's availability status",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def patch_my_status(
    body: AgentStatusUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AgentProfileResponse:
    async with uow.transactional() as session:
        prof = (
            await session.execute(
                select(CCAgentProfile).where(CCAgentProfile.user_id == principal.user_id)
            )
        ).scalar_one_or_none()
        if prof is None:
            prof = CCAgentProfile(user_id=principal.user_id, status=body.status)
            session.add(prof)
        else:
            prof.status = body.status
            prof.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return AgentProfileResponse(
            user_id=prof.user_id, email=None, full_name=None,
            status=prof.status, max_active_chats=prof.max_active_chats,
            current_active_chats=prof.current_active_chats,
            language_skill=prof.language_skill,
        )


# ─── Per-agent SIP softphone credentials (added 2026-05-16) ─────────
@router.get(
    "/me/softphone",
    summary="Fetch the current agent's SIP softphone credentials.",
    description=(
        "Returns the per-agent SIP extension + password + SBC WebSocket "
        "URI + SIP domain that the admin-panel softphone widget needs "
        "to REGISTER against the configured telephony provider. Per-agent "
        "creds are provisioned out-of-band by an admin via "
        "`PUT /admin/customer-care/agents/{id}/softphone`. "
        "Gated on either `customercare.agent` or `voice_call.assign` so "
        "any voice-capable agent can fetch their own creds."
    ),
)
async def get_my_softphone(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, object]:
    # Hand-rolled perm check (no Depends) so we can accept either of
    # two perms — the requires_permission factory only handles single
    # perms. Voice-call ops folks (support_agent + admin) should fetch
    # without needing the CC agent role too.
    if not principal.has_permission(_AGENT) and not principal.has_permission(_VOICE_CALL_ASSIGN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": (
                    f"Missing required permission(s): {_AGENT} or {_VOICE_CALL_ASSIGN}"
                ),
            },
        )
    import os
    ws_uri = os.environ.get("BL_HUB_WS_URI") or ""
    sip_domain = os.environ.get("BL_HUB_SIP_DOMAIN") or ""
    async with uow.transactional() as session:
        prof = (
            await session.execute(
                select(CCAgentProfile).where(CCAgentProfile.user_id == principal.user_id)
            )
        ).scalar_one_or_none()
        if prof is None:
            return {
                "provisioned": False,
                "reason": "no_agent_profile",
                "hint": "Hit /customer-care/me first to auto-create the profile, then ask an admin to provision the extension.",
            }
        if not prof.sip_extension or not prof.sip_password_enc:
            return {
                "provisioned": False,
                "reason": "no_sip_credentials",
                "hint": "Admin must run PUT /admin/customer-care/agents/{user_id}/softphone with extension + password.",
            }
        if not ws_uri or not sip_domain:
            return {
                "provisioned": False,
                "reason": "sbc_not_configured",
                "hint": "Set BL_HUB_WS_URI + BL_HUB_SIP_DOMAIN env vars on the backend.",
            }
        return {
            "provisioned": True,
            "extension": prof.sip_extension,
            "password": prof.sip_password_enc,
            "ws_uri": ws_uri,
            "sip_domain": sip_domain,
            "sip_uri": f"sip:{prof.sip_extension}@{sip_domain}",
        }


@router.put(
    "/admin/agents/{user_id}/softphone",
    summary="Admin: provision or rotate an agent's SIP softphone credentials.",
    description=(
        "Stores the agent's SBC extension + password so the softphone "
        "widget can REGISTER. The extension is provisioned out-of-band "
        "on the telephony provider's portal (Banglalink HUB); this "
        "endpoint just records the credentials inside Hypershop so the "
        "agent can fetch them via `GET /customer-care/me/softphone`. "
        "Gated on `customercare.admin` — voice-routing supervisors and "
        "above. Pass `extension=null` to deprovision."
    ),
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def admin_set_agent_softphone(
    user_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    # extension=None means "deprovision". Pydantic treats `Body(...)`
    # as required-non-null, so we use `default=None` to accept explicit
    # null payloads alongside missing field.
    extension: str | None = Body(default=None, embed=True),
    password: str | None = Body(default=None, embed=True),
) -> dict[str, object]:
    async with uow.transactional() as session:
        prof = (
            await session.execute(
                select(CCAgentProfile).where(CCAgentProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if prof is None:
            # Auto-create so admins don't have to drive a separate
            # "make this user an agent" call.
            prof = CCAgentProfile(user_id=user_id, status="offline")
            session.add(prof)
            await session.flush()
        if extension is None:
            prof.sip_extension = None
            prof.sip_password_enc = None
            action = "deprovisioned"
        else:
            if not password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": "password_required_when_setting_extension"},
                )
            prof.sip_extension = extension.strip()
            prof.sip_password_enc = password
            action = "provisioned"
        await record_audit(
            actor=principal,
            action=f"customer_care.softphone.{action}",
            resource_type="cc_agent_profile",
            resource_id=user_id,
            metadata={"extension": extension},
        )
        return {
            "user_id": str(user_id),
            "action": action,
            "extension": prof.sip_extension,
        }


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummary,
    summary="Top-of-page counters for the agent dashboard",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def get_dashboard_summary(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> DashboardSummary:
    async with uow.transactional() as session:
        data = await service.dashboard_summary(session)
        return DashboardSummary(**data)


@router.get(
    "/agents",
    response_model=list[AgentProfileResponse],
    summary="List users with a customer-care agent profile (for transfer / assignment UI)",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def list_agents(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(
        default=None, alias="status",
        pattern=r"^(online|busy|away|offline)$",
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AgentProfileResponse]:
    """Returns every user that has a ``cc_agent_profile`` row, joined
    with their core user fields. Used by the transfer dropdown +
    "online agents" sidebar in the dashboard.
    """
    from sqlalchemy import text as _t
    sql = (
        "SELECT u.id, u.email::text, u.full_name, "
        "       p.status, p.max_active_chats, p.current_active_chats, "
        "       p.language_skill "
        "FROM cc_agent_profile p "
        "JOIN users u ON u.id = p.user_id "
    )
    params: dict[str, object] = {"lim": limit}
    if status_filter:
        sql += "WHERE p.status = :st "
        params["st"] = status_filter
    sql += (
        "ORDER BY p.status = 'online' DESC, p.current_active_chats ASC "
        "LIMIT :lim"
    )
    async with uow.transactional() as session:
        rows = (await session.execute(_t(sql), params)).all()
        return [
            AgentProfileResponse(
                user_id=r[0], email=r[1], full_name=r[2],
                status=r[3], max_active_chats=r[4],
                current_active_chats=r[5], language_skill=r[6],
            )
            for r in rows
        ]


# ---------------------------------------------------------------- conversations
@router.get(
    "/conversations",
    response_model=list[ConversationSummary],
    summary="List conversations (defaults to assigned-to-me + open)",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def list_conversations(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    scope: str = Query(default="mine", pattern=r"^(mine|unassigned|all)$"),
    status_filter: str | None = Query(default="open", alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ConversationSummary]:
    async with uow.transactional() as session:
        stmt = select(CCConversation)
        if scope == "mine":
            stmt = stmt.where(CCConversation.agent_id == principal.user_id)
        elif scope == "unassigned":
            stmt = stmt.where(CCConversation.agent_id.is_(None))
        if status_filter:
            stmt = stmt.where(CCConversation.status == status_filter)
        stmt = stmt.order_by(desc(CCConversation.last_message_at)).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        out: list[ConversationSummary] = []
        for c in rows:
            cust = await _load_customer_summary(session, c.customer_id)
            out.append(
                ConversationSummary(
                    id=c.id,
                    customer_id=c.customer_id,
                    customer_phone=cust["phone"],
                    agent_id=c.agent_id,
                    channel=c.channel,
                    status=c.status,
                    priority=c.priority,
                    last_message=c.last_message,
                    last_message_at=c.last_message_at,
                    handover_required=c.handover_required,
                    sla_first_response_breached=c.sla_first_response_breached,
                    sla_resolution_breached=c.sla_resolution_breached,
                )
            )
        return out


@router.get(
    "/conversations/{conv_id}",
    response_model=ConversationDetail,
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def get_conversation(
    conv_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ConversationDetail:
    async with uow.transactional() as session:
        c = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if c is None:
            raise NotFoundError("Conversation not found")
        cust = await _load_customer_summary(session, c.customer_id)
        msgs = (
            await session.execute(
                select(CCMessage)
                .where(CCMessage.conversation_id == c.id)
                .order_by(CCMessage.created_at.asc())
            )
        ).scalars().all()
        return ConversationDetail(
            id=c.id,
            customer_id=c.customer_id,
            customer_phone=cust["phone"],
            agent_id=c.agent_id,
            channel=c.channel,
            status=c.status,
            priority=c.priority,
            last_message=c.last_message,
            last_message_at=c.last_message_at,
            handover_required=c.handover_required,
            sla_first_response_breached=c.sla_first_response_breached,
            sla_resolution_breached=c.sla_resolution_breached,
            customer_name=cust["full_name"],
            preferred_language=cust["preferred_language"],
            consent_status=cust["consent_status"],
            source=c.source,
            first_response_at=c.first_response_at,
            resolved_at=c.resolved_at,
            handover_reason=c.handover_reason,
            order_id=c.order_id,
            messages=[
                MessageResponse(
                    id=m.id, conversation_id=m.conversation_id,
                    sender_type=m.sender_type, message_type=m.message_type,
                    message_body=m.message_body, media_url=m.media_url,
                    channel=m.channel, whatsapp_message_id=m.whatsapp_message_id,
                    ai_confidence=m.ai_confidence, created_at=m.created_at,
                )
                for m in msgs
            ],
        )


@router.post(
    "/conversations/{conv_id}/messages",
    response_model=MessageResponse,
    status_code=201,
    summary="Agent sends a message in this conversation",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def send_message(
    conv_id: Annotated[UUID, Path(...)],
    body: SendMessageRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> MessageResponse:
    # Per-agent send rate limit (sprint 5)
    from app.modules.customer_care.api.sprint5 import agent_rate_check
    allowed, count = agent_rate_check(principal.user_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "agent_rate_limited", "messages_in_window": count},
        )
    async with uow.transactional() as session:
        conv = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise NotFoundError("Conversation not found")
        if conv.status == "resolved":
            raise BusinessRuleError("Conversation is resolved — reopen first")
        # Auto-claim if unassigned
        if conv.agent_id is None:
            await service.assign_agent(session, conversation=conv, agent_id=principal.user_id)
        msg = await service.append_message(
            session, conversation=conv, sender_type="agent",
            body=body.body, message_type=body.message_type,
            media_url=body.media_url,
        )
        # Resolve customer phone for outbound dispatch (after flush so
        # the message row is durable even if WhatsApp send fails).
        cust = await _load_customer_summary(session, conv.customer_id)
        to_phone = cust.get("phone")
        await record_audit(
            actor=principal,
            action="customer_care.message.sent",
            resource_type="cc_messages",
            resource_id=msg.id,
            metadata={
                "conversation_id": str(conv.id),
                "message_type": body.message_type,
            },
        )
    # Push SSE event so other agents' inboxes refresh
    sse_bus.publish({
        "type": "message.sent",
        "conversation_id": str(conv_id),
        "preview": (body.body or "")[:80],
    })
    # Outside the txn — WhatsApp call is network IO. If it fails we
    # still return success because the message is durably stored;
    # the SLA scanner / retry queue can re-dispatch later.
    wa_msg_id: str | None = None
    if to_phone and conv.channel == "whatsapp":
        if body.message_type == "image" and body.media_url:
            result = await outbound.send_whatsapp_image(
                to_phone=to_phone, image_url=body.media_url, caption=body.body,
            )
        else:
            result = await outbound.send_whatsapp_text(
                to_phone=to_phone, body=body.body,
            )
        if result is not None:
            wa_msg_id = ((result.get("messages") or [{}])[0]).get("id")
            if wa_msg_id:
                # Best-effort: persist the WhatsApp message id back
                async with uow.transactional() as session:
                    await session.execute(
                        CCMessage.__table__.update()
                        .where(CCMessage.id == msg.id)
                        .values(whatsapp_message_id=wa_msg_id),
                    )
    return MessageResponse(
        id=msg.id, conversation_id=msg.conversation_id,
        sender_type=msg.sender_type, message_type=msg.message_type,
        message_body=msg.message_body, media_url=msg.media_url,
        channel=msg.channel,
        whatsapp_message_id=wa_msg_id or msg.whatsapp_message_id,
        ai_confidence=msg.ai_confidence, created_at=msg.created_at,
    )


@router.post(
    "/conversations/{conv_id}/transfer",
    response_model=ConversationSummary,
    summary="Transfer this conversation to another agent",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def transfer_conversation(
    conv_id: Annotated[UUID, Path(...)],
    body: TransferConversationRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ConversationSummary:
    async with uow.transactional() as session:
        conv = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise NotFoundError("Conversation not found")
        if conv.agent_id and conv.agent_id != body.target_agent_id:
            await service.decrement_agent_load(session, agent_id=conv.agent_id)
        await service.assign_agent(
            session, conversation=conv, agent_id=body.target_agent_id,
        )
        if body.reason:
            conv.handover_reason = body.reason
        cust = await _load_customer_summary(session, conv.customer_id)
        await record_audit(
            actor=principal,
            action="customer_care.conversation.transferred",
            resource_type="cc_conversation",
            resource_id=conv.id,
            metadata={
                "target_agent_id": str(body.target_agent_id),
                "reason": body.reason,
            },
        )
        result = ConversationSummary(
            id=conv.id, customer_id=conv.customer_id,
            customer_phone=cust["phone"], agent_id=conv.agent_id,
            channel=conv.channel, status=conv.status, priority=conv.priority,
            last_message=conv.last_message, last_message_at=conv.last_message_at,
            handover_required=conv.handover_required,
            sla_first_response_breached=conv.sla_first_response_breached,
            sla_resolution_breached=conv.sla_resolution_breached,
        )
    sse_bus.publish(
        {"type": "conversation.transferred",
         "conversation_id": str(conv_id),
         "target_agent_id": str(body.target_agent_id)},
        agent_id=body.target_agent_id,
    )
    return result


@router.post(
    "/conversations/{conv_id}/resolve",
    response_model=ConversationSummary,
    summary="Mark the conversation as resolved",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def resolve_conversation(
    conv_id: Annotated[UUID, Path(...)],
    body: ResolveConversationRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ConversationSummary:
    async with uow.transactional() as session:
        conv = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise NotFoundError("Conversation not found")
        conv.status = "resolved"
        conv.resolved_at = datetime.now(timezone.utc)
        if body.resolution_note:
            await service.append_message(
                session, conversation=conv, sender_type="system",
                body=f"[resolution] {body.resolution_note}",
            )
        if conv.agent_id:
            await service.decrement_agent_load(session, agent_id=conv.agent_id)
        await record_audit(
            actor=principal,
            action="customer_care.conversation.resolved",
            resource_type="cc_conversation",
            resource_id=conv.id,
            metadata={"resolution_note": body.resolution_note},
        )
        # Pull transcript inside the txn so we have it for the
        # post-resolve summary generation step (fire-and-forget below)
        _summary_rows = (
            await session.execute(
                _sa_text(
                    "SELECT sender_type, message_body FROM cc_messages "
                    "WHERE conversation_id = :c ORDER BY created_at ASC LIMIT 100"
                ),
                {"c": conv.id},
            )
        ).all()
        _summary_conv_id = conv.id
        cust = await _load_customer_summary(session, conv.customer_id)
        # Build the response object NOW while conv is still attached
        _resp = ConversationSummary(
            id=conv.id, customer_id=conv.customer_id,
            customer_phone=cust["phone"], agent_id=conv.agent_id,
            channel=conv.channel, status=conv.status, priority=conv.priority,
            last_message=conv.last_message, last_message_at=conv.last_message_at,
            handover_required=conv.handover_required,
            sla_first_response_breached=conv.sla_first_response_breached,
            sla_resolution_breached=conv.sla_resolution_breached,
        )
    # Fire-and-forget AI summary (OpenAI call — must be outside the txn).
    # If AI is unavailable, the summary stays NULL and an admin can
    # trigger /conversations/{id}/summary later.
    try:
        from app.modules.customer_care import ai as _cc_ai
        transcript = [{"sender_type": r[0], "body": r[1] or ""} for r in _summary_rows]
        summary_text = await _cc_ai.summarize_conversation(transcript)
        if summary_text:
            async with uow.transactional() as session2:
                await session2.execute(
                    _sa_text(
                        "UPDATE cc_conversations SET ai_summary = :s, "
                        "summary_generated_at = now() WHERE id = :c"
                    ),
                    {"s": summary_text, "c": _summary_conv_id},
                )
    except Exception:  # noqa: BLE001 — never block on AI
        pass
    return _resp


# ---------------------------------------------------------------- customer profile
@router.get(
    "/customers/{customer_id}",
    response_model=CustomerProfileResponse,
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def get_customer_profile(
    customer_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> CustomerProfileResponse:
    async with uow.transactional() as session:
        cust = await _load_customer_summary(session, customer_id)
        prof = (
            await session.execute(
                select(CCCustomerProfile).where(CCCustomerProfile.customer_id == customer_id)
            )
        ).scalar_one_or_none()
        if prof is None:
            raise NotFoundError("Customer profile not found")
        return CustomerProfileResponse(
            customer_id=customer_id, full_name=cust["full_name"],
            phone=cust["phone"], preferred_language=prof.preferred_language,
            consent_status=prof.consent_status,
            assigned_agent_id=prof.assigned_agent_id,
            last_interest=prof.last_interest, cc_status=prof.cc_status,
        )


# ---------------------------------------------------------------- followups
@router.post(
    "/followups",
    response_model=FollowupResponse,
    status_code=201,
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def create_followup(
    body: FollowupCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> FollowupResponse:
    async with uow.transactional() as session:
        row = CCFollowup(
            customer_id=body.customer_id,
            product_id=body.product_id,
            campaign_name=body.campaign_name,
            next_followup_at=body.next_followup_at,
        )
        session.add(row)
        await session.flush()
        return FollowupResponse(
            id=row.id, customer_id=row.customer_id,
            product_id=row.product_id, campaign_name=row.campaign_name,
            stage=row.stage, status=row.status,
            last_sent_at=row.last_sent_at, next_followup_at=row.next_followup_at,
        )


@router.get(
    "/followups",
    response_model=list[FollowupResponse],
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def list_followups(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[FollowupResponse]:
    async with uow.transactional() as session:
        stmt = select(CCFollowup).order_by(desc(CCFollowup.created_at)).limit(limit)
        if status_filter:
            stmt = stmt.where(CCFollowup.status == status_filter)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            FollowupResponse(
                id=r.id, customer_id=r.customer_id, product_id=r.product_id,
                campaign_name=r.campaign_name, stage=r.stage, status=r.status,
                last_sent_at=r.last_sent_at, next_followup_at=r.next_followup_at,
            )
            for r in rows
        ]


# ---------------------------------------------------------------- WhatsApp webhook
@router.get(
    "/webhooks/whatsapp",
    summary="Meta WhatsApp Cloud API webhook verification (challenge handshake)",
)
async def whatsapp_verify(
    mode: str = Query(default="", alias="hub.mode"),
    token: str = Query(default="", alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
):
    # Verification token is read from settings; CC's original
    # whatsapp_verify_token. We match against the env var directly
    # to avoid coupling to CC's config object.
    import os
    expected = os.environ.get("CC_WHATSAPP_VERIFY_TOKEN", "hypershop-cc")
    if mode == "subscribe" and token == expected:
        return int(challenge) if challenge.isdigit() else challenge
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid verify token")


# Channel-aware in-process rate-limit bucket. Caps to N requests/min
# per (channel, source-IP). Defensive — Meta's actual webhook IPs are
# allow-listed at the firewall in prod; this is a belt-and-braces
# against accidental floods (replay, debug loops). Configurable via
# env: CC_WEBHOOK_RATE_LIMIT_PER_MIN.
import os as _os
import time as _time
from collections import deque as _deque
_WEBHOOK_RATE_LIMIT = int(_os.environ.get("CC_WEBHOOK_RATE_LIMIT_PER_MIN", "600"))
_webhook_bucket: dict[str, _deque] = {}


def _webhook_rate_check(key: str) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = _time.monotonic()
    window_start = now - 60.0
    q = _webhook_bucket.setdefault(key, _deque(maxlen=_WEBHOOK_RATE_LIMIT * 2))
    # Drop timestamps older than the window
    while q and q[0] < window_start:
        q.popleft()
    if len(q) >= _WEBHOOK_RATE_LIMIT:
        return False
    q.append(now)
    return True


@router.post(
    "/webhooks/whatsapp",
    summary="WhatsApp Cloud API inbound message webhook",
)
async def whatsapp_inbound(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict:
    """Ingest a WhatsApp inbound message. Idempotent on
    (channel, whatsapp_message_id). Creates conversation if needed.

    Verifies the Meta X-Hub-Signature-256 HMAC over the raw body when
    ``WHATSAPP_APP_SECRET`` is configured. Missing/bad signatures are
    rejected with 403; signature verification is skipped (with a log
    line) only when no secret is set, to keep dev easy.
    """
    # Rate-limit (per-channel + source-IP). Returns 429 when bucket
    # is full so a hostile / runaway sender can't OOM us.
    client_ip = request.client.host if request.client else "unknown"
    if not _webhook_rate_check(f"whatsapp:{client_ip}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited", "channel": "whatsapp"},
        )
    raw_body = await request.body()
    ok, reason = outbound.verify_meta_signature(
        raw_body=raw_body, signature_header=x_hub_signature_256,
    )
    if not ok:
        import logging
        logging.getLogger("hypershop.customer_care").warning(
            "whatsapp_webhook_signature_rejected", extra={"reason": reason},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "invalid_webhook_signature", "reason": reason},
        )
    try:
        import json as _json
        payload = _json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_json",
        )
    entries = payload.get("entry") or []
    messages_ingested = 0
    for entry in entries:
        changes = entry.get("changes") or []
        for change in changes:
            value = change.get("value") or {}
            contacts = value.get("contacts") or []
            phone_by_wa_id: dict[str, dict] = {
                str(c.get("wa_id") or ""): c for c in contacts
            }
            for msg in value.get("messages") or []:
                wa_msg_id = str(msg.get("id") or "")
                from_phone = "+" + str(msg.get("from") or "")
                msg_kind = str(msg.get("type") or "text")
                text_body = (msg.get("text") or {}).get("body")
                media_url: str | None = None
                media_id: str | None = None
                # Meta inbound image / document / audio carry a media id
                # we resolve to a URL via the media-download endpoint.
                # For now, store the Meta media id reference so the agent
                # UI can request the binary on demand; mime/url retrieval
                # is deferred to a background fetch.
                if msg_kind in ("image", "audio", "video", "document"):
                    media_id = ((msg.get(msg_kind) or {}).get("id"))
                    if media_id:
                        media_url = f"meta-media://{media_id}"
                    caption = (msg.get(msg_kind) or {}).get("caption")
                    if caption and not text_body:
                        text_body = caption
                    # Voice notes: best-effort Whisper transcription
                    # so the agent inbox shows the text instead of a
                    # cryptic media reference. Inline call — fine for
                    # a handful per minute; for bulk traffic move to
                    # an ARQ queue.
                    if msg_kind == "audio" and media_id:
                        try:
                            dl = await outbound.download_whatsapp_media(media_id=media_id)
                            if dl is not None:
                                audio_bytes, mime = dl
                                transcript = await outbound.transcribe_voice_audio(
                                    audio_bytes=audio_bytes, mime_type=mime,
                                )
                                if transcript:
                                    text_body = transcript
                                    msg_kind = "text"  # treat as text downstream
                        except Exception as e:  # noqa: BLE001
                            import logging
                            logging.getLogger("hypershop.customer_care").warning(
                                "voice_transcription_failed", extra={"error": str(e)},
                            )
                contact = phone_by_wa_id.get(msg.get("from") or "") or {}
                contact_name = (contact.get("profile") or {}).get("name")
                if not wa_msg_id or not from_phone:
                    continue
                async with uow.transactional() as session:
                    # Idempotency
                    exists = (
                        await session.execute(
                            select(CCWebhookIdempotency).where(
                                CCWebhookIdempotency.channel == "whatsapp",
                                CCWebhookIdempotency.channel_message_id == wa_msg_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if exists:
                        continue
                    session.add(CCWebhookIdempotency(
                        channel="whatsapp", channel_message_id=wa_msg_id,
                    ))
                    customer_id = await service.resolve_or_create_customer_by_phone(
                        session, phone=from_phone, default_name=contact_name,
                    )
                    # Find or create an open conversation for this customer
                    conv = (
                        await session.execute(
                            select(CCConversation)
                            .where(CCConversation.customer_id == customer_id)
                            .where(CCConversation.status == "open")
                            .order_by(desc(CCConversation.last_message_at))
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if conv is None:
                        conv = CCConversation(
                            customer_id=customer_id,
                            channel="whatsapp",
                            source="whatsapp",
                            status="open",
                        )
                        session.add(conv)
                        await session.flush()
                        agent_id = await service.choose_agent(session)
                        if agent_id:
                            await service.assign_agent(
                                session, conversation=conv, agent_id=agent_id,
                            )
                    new_msg = await service.append_message(
                        session, conversation=conv, sender_type="customer",
                        body=text_body, channel="whatsapp",
                        whatsapp_message_id=wa_msg_id,
                        message_type=msg_kind if msg_kind in ("image", "audio", "video", "document") else "text",
                        media_url=media_url,
                    )
                    messages_ingested += 1
                    # Inline AI classification (sprint 6). Best-effort
                    # — if AI is unavailable / fails we silently skip.
                    try:
                        from app.modules.customer_care import ai as _cc_ai
                        if text_body and text_body.strip():
                            clf = await _cc_ai.classify_message(text_body)
                            if clf:
                                await session.execute(
                                    _sa_text(
                                        "UPDATE cc_messages "
                                        "SET sentiment = :s, sentiment_score = :sc, "
                                        "    intent_tag = :it WHERE id = :m"
                                    ),
                                    {
                                        "s": clf["sentiment"],
                                        "sc": clf["sentiment_score"],
                                        "it": clf["intent_tag"],
                                        "m": new_msg.id,
                                    },
                                )
                                # Also auto-tag the conversation when
                                # we have a high-signal intent
                                if clf["intent_tag"] in ("refund", "cancel", "complaint"):
                                    await session.execute(
                                        _sa_text(
                                            "UPDATE cc_conversations "
                                            "SET tags = array(SELECT DISTINCT unnest("
                                            "  COALESCE(tags, ARRAY[]::varchar[]) || ARRAY[:t]::varchar[]"
                                            ")), priority = "
                                            "CASE WHEN :t IN ('refund','complaint') "
                                            "  THEN 'high' ELSE priority END "
                                            "WHERE id = :c"
                                        ),
                                        {"t": clf["intent_tag"], "c": conv.id},
                                    )
                    except Exception as e:  # noqa: BLE001 — never block on AI
                        import logging
                        logging.getLogger("hypershop.customer_care").warning(
                            "inline_ai_classify_failed", extra={"error": str(e)},
                        )
                    # SSE notify: new message in this conversation
                    sse_bus.publish({
                        "type": "message.received",
                        "conversation_id": str(conv.id),
                        "customer_phone": from_phone,
                        "preview": (text_body or f"<{msg_kind}>")[:80],
                    }, agent_id=conv.agent_id)
                    # Stash data for AI auto-reply OUTSIDE the txn
                    _ai_q = (
                        text_body,
                        conv.id,
                        from_phone,
                        # Pull preferred language for the AI tone
                        (await _load_customer_summary(
                            session, conv.customer_id,
                        )).get("preferred_language") or "bangla",
                    )
                # AI auto-reply (no DB lock held). Best-effort; if it
                # fails the customer is not blocked and an agent can
                # still pick up the conversation manually.
                try:
                    body_text, conv_id, phone, lang = _ai_q
                    # After-hours: send canned reply, skip the OpenAI call
                    from app.modules.customer_care.api.sprint5 import _is_after_hours
                    if _is_after_hours():
                        import os
                        ai_text = (
                            os.environ.get("CC_AFTER_HOURS_REPLY_EN")
                            or "Thanks for your message! We're currently closed. "
                            "We'll reply when we reopen at 9am."
                        ) if lang == "english" else (
                            os.environ.get("CC_AFTER_HOURS_REPLY_BN")
                            or "ধন্যবাদ! আমরা এখন বন্ধ। সকাল ৯টায় আপনার মেসেজের উত্তর দেব।"
                        )
                        ai_conf = Decimal("0.99")
                        handover = False
                    else:
                        # Try RAG retrieval to ground the answer
                        rag_ctx = await _try_rag_context(uow, body_text or "")
                        ai_text, ai_conf, handover = await outbound.generate_ai_reply(
                            customer_text=body_text or "",
                            customer_language=lang,
                            rag_context=rag_ctx,
                        )
                    async with uow.transactional() as session2:
                        conv2 = (
                            await session2.execute(
                                select(CCConversation).where(CCConversation.id == conv_id)
                            )
                        ).scalar_one()
                        await service.append_message(
                            session2, conversation=conv2, sender_type="ai",
                            body=ai_text, channel="whatsapp",
                        )
                        # AI confidence + handover state on the conv
                        if handover:
                            conv2.handover_required = True
                            conv2.handover_reason = "ai_handover"
                    if phone:
                        await outbound.send_whatsapp_text(
                            to_phone=phone, body=ai_text,
                        )
                except Exception as e:  # noqa: BLE001 — never break webhook on AI failure
                    import logging
                    logging.getLogger("hypershop.customer_care").warning(
                        "ai_auto_reply_failed", extra={"error": str(e)},
                    )
    return {"received": True, "messages_ingested": messages_ingested}


# ─── Short-form perm real implementations (2026-05-16) ───────────────
# Concrete handlers behind `ai_care.view` + `voice_call.assign` perms.
# Both write to durable tables; voice_call.assign also emits an outbox
# event so the softphone/SSE worker can react to the assignment.


@router.get(
    "/ai-care/triage",
    summary="Read the AI-flagged conversation triage queue.",
    description=(
        "Lists CC conversations the assistive model flagged as needing "
        "human review: handover_required, SLA breaches, high-priority, "
        "or low-confidence last AI message. Sorted urgent-first. Gated "
        "on `ai_care.view` (super_admin / admin / ai_manager / "
        "support_agent)."
    ),
    dependencies=[Depends(requires_permission(_AI_CARE_VIEW))],
)
async def ai_care_triage(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: int = Query(default=50, ge=1, le=200),
    min_confidence: float = Query(
        default=0.70, ge=0.0, le=1.0,
        description="Conversations whose last AI message scored below "
                    "this confidence are surfaced as triage candidates.",
    ),
) -> dict[str, object]:
    """Pulls the union of three triage signals into one ranked list:

    1. handover_required = true (AI gave up, asked for human)
    2. sla_first_response_breached OR sla_resolution_breached
    3. last AI message confidence < min_confidence threshold
    """
    from sqlalchemy import text as _t
    sql = _t(
        """
        WITH last_ai AS (
            SELECT DISTINCT ON (conversation_id)
                conversation_id,
                ai_confidence
            FROM cc_messages
            WHERE sender_type = 'ai'
            ORDER BY conversation_id, created_at DESC
        )
        SELECT
            c.id,
            c.customer_id,
            c.agent_id,
            c.channel,
            c.status,
            c.priority,
            c.handover_required,
            c.handover_reason,
            c.sla_first_response_breached,
            c.sla_resolution_breached,
            c.last_message,
            c.last_message_at,
            la.ai_confidence,
            CASE
                WHEN c.handover_required THEN 'handover_required'
                WHEN c.sla_resolution_breached THEN 'sla_resolution_breach'
                WHEN c.sla_first_response_breached THEN 'sla_first_response_breach'
                WHEN c.priority IN ('high','urgent') THEN 'priority_high'
                WHEN la.ai_confidence IS NOT NULL
                     AND la.ai_confidence < :minc THEN 'low_ai_confidence'
                ELSE NULL
            END AS triage_reason
        FROM cc_conversations c
        LEFT JOIN last_ai la ON la.conversation_id = c.id
        WHERE c.status != 'resolved'
          AND (
              c.handover_required
              OR c.sla_first_response_breached
              OR c.sla_resolution_breached
              OR c.priority IN ('high','urgent')
              OR (la.ai_confidence IS NOT NULL AND la.ai_confidence < :minc)
          )
        ORDER BY
            c.handover_required DESC,
            c.sla_resolution_breached DESC,
            c.sla_first_response_breached DESC,
            CASE c.priority
                WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                WHEN 'normal' THEN 2 WHEN 'low' THEN 3 ELSE 4
            END,
            COALESCE(la.ai_confidence, 1.0) ASC,
            c.last_message_at DESC
        LIMIT :lim
        """
    )
    async with uow.transactional() as session:
        rows = (
            await session.execute(sql, {"minc": min_confidence, "lim": limit})
        ).all()
        items = [
            {
                "conversation_id": str(r[0]),
                "customer_id": str(r[1]),
                "agent_id": str(r[2]) if r[2] else None,
                "channel": r[3],
                "status": r[4],
                "priority": r[5],
                "handover_required": r[6],
                "handover_reason": r[7],
                "sla_first_response_breached": r[8],
                "sla_resolution_breached": r[9],
                "last_message": (r[10] or "")[:200],
                "last_message_at": r[11].isoformat() if r[11] else None,
                "last_ai_confidence": float(r[12]) if r[12] is not None else None,
                "triage_reason": r[13],
            }
            for r in rows
        ]
    return {
        "operator": str(principal.user_id),
        "min_confidence": min_confidence,
        "total": len(items),
        "items": items,
    }


@router.post(
    "/voice-calls/{call_id}/assign",
    summary="Route an inbound voice call to a specific agent.",
    description=(
        "Voice-call dispatch: claim a ringing inbound call for a named "
        "agent. Asserts state=ringing, writes assignee, appends a state "
        "transition row, and enqueues `voice.call.assigned` to outbox "
        "so the agent's softphone can alert via SSE/websocket. Gated "
        "on `voice_call.assign` (super_admin / admin / support_agent / "
        "customercare_admin only — NOT the basic customercare_agent, "
        "since routing is a supervisor task in this org)."
    ),
    dependencies=[Depends(requires_permission(_VOICE_CALL_ASSIGN))],
)
async def assign_voice_call(
    call_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    agent_id: UUID = Body(..., embed=True),
) -> dict[str, object]:
    from app.modules.customer_care import voice_calls as _vc
    async with uow.transactional() as session:
        call = await _vc.assign_to_agent(
            session,
            call_id=call_id,
            agent_id=agent_id,
            actor_id=principal.user_id,
        )
        await record_audit(
            actor=principal,
            action="voice_call.assigned",
            resource_type="cc_voice_calls",
            resource_id=call.id,
            metadata={"target_agent_id": str(agent_id)},
        )
        result = {
            "call_id": str(call.id),
            "provider": call.provider,
            "provider_call_id": call.provider_call_id,
            "status": call.status,
            "agent_id": str(call.agent_id) if call.agent_id else None,
            "from_phone": call.from_phone,
            "assigned_at": call.assigned_at.isoformat() if call.assigned_at else None,
            "assigned_by": str(principal.user_id),
        }
    sse_bus.publish(
        {
            "type": "voice.call.assigned",
            "call_id": str(call_id),
            "agent_id": str(agent_id),
        },
        agent_id=agent_id,
    )
    return result


@router.post(
    "/voice-calls/webhooks/banglalink-hub",
    summary="Banglalink HUB inbound voice-call webhook (signed).",
    description=(
        "Public endpoint — Banglalink HUB SBC POSTs here when a call "
        "rings, gets answered, or ends. Verifies the `X-BL-Signature` "
        "HMAC-SHA256 over the raw body using `BL_HUB_WEBHOOK_SECRET`. "
        "Idempotent on `(provider, provider_call_id)`. Maps the BL event "
        "to our internal `ringing`/`answered`/`ended`/`missed` vocabulary "
        "via `voice_calls.ingest_inbound`/`mark_answered`/`mark_ended`/"
        "`mark_missed`. Adapter at `external_adapters/banglalink_hub.py`."
    ),
)
async def banglalink_hub_webhook(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, object]:
    from app.modules.customer_care import voice_calls as _vc
    from app.modules.customer_care.external_adapters.banglalink_hub import (
        get_adapter as _get_bl_adapter,
    )
    adapter = _get_bl_adapter()
    raw_body = await request.body()
    ok, reason = adapter.verify_webhook_signature(
        raw_body=raw_body, headers=dict(request.headers),
    )
    if not ok:
        import logging
        logging.getLogger("hypershop.customer_care").warning(
            "bl_hub_webhook_signature_rejected", extra={"reason": reason},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "invalid_webhook_signature"},
        )
    try:
        import json as _json
        payload = _json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_json",
        )
    try:
        evt = adapter.parse_inbound_event(payload)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unsupported_payload", "reason": str(e)},
        )
    async with uow.transactional() as session:
        if evt.event_type == "ringing":
            call = await _vc.ingest_inbound(
                session,
                provider=evt.provider,
                provider_call_id=evt.provider_call_id,
                from_phone=evt.from_phone,
                to_number=evt.to_number,
                extra=evt.raw,
            )
            return {"received": True, "voice_call_id": str(call.id)}
        # Lookup existing call for non-ringing events.
        from sqlalchemy import select as _select
        from app.modules.customer_care.models import CCVoiceCall
        existing = (
            await session.execute(
                _select(CCVoiceCall).where(
                    CCVoiceCall.provider == evt.provider,
                    CCVoiceCall.provider_call_id == evt.provider_call_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            # Late/out-of-order event — log + 200 (don't 404, BL would retry).
            import logging
            logging.getLogger("hypershop.customer_care").info(
                "bl_hub_webhook_unknown_call",
                extra={
                    "event_type": evt.event_type,
                    "provider_call_id": evt.provider_call_id,
                },
            )
            return {"received": True, "voice_call_id": None, "note": "unknown_call"}
        if evt.event_type == "answered":
            await _vc.mark_answered(session, call_id=existing.id)
        elif evt.event_type == "ended":
            await _vc.mark_ended(
                session, call_id=existing.id, actor_id=None,
                recording_url=evt.raw.get("recording_url"),
            )
        elif evt.event_type == "missed":
            await _vc.mark_missed(session, call_id=existing.id)
        return {"received": True, "voice_call_id": str(existing.id)}


@router.post(
    "/voice-calls/_ingest",
    summary="Provider webhook: ingest an inbound ringing voice call.",
    description=(
        "Provider-neutral ingest. Idempotent on (provider, provider_call_id). "
        "Designed to be called by the telephony adapter (Twilio webhook, "
        "Exotel callback, etc.) after signature verification at the adapter "
        "layer. Requires `voice_call.assign` since only voice-call operators "
        "should be able to register a call (in prod this endpoint is typically "
        "called by a service account that holds the perm)."
    ),
    dependencies=[Depends(requires_permission(_VOICE_CALL_ASSIGN))],
    status_code=201,
)
async def ingest_voice_call(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    provider: str = Body(..., embed=True),
    provider_call_id: str = Body(..., embed=True),
    from_phone: str = Body(..., embed=True),
    to_number: str | None = Body(default=None, embed=True),
    priority: str = Body(default="normal", embed=True),
) -> dict[str, object]:
    from app.modules.customer_care import voice_calls as _vc
    async with uow.transactional() as session:
        call = await _vc.ingest_inbound(
            session,
            provider=provider,
            provider_call_id=provider_call_id,
            from_phone=from_phone,
            to_number=to_number,
            priority=priority,
        )
        # ACD: auto-route to a free agent, else queue + caller wait message.
        dispatch = await _vc.dispatch_inbound(
            session, call=call, actor_id=principal.user_id,
        )
        return {
            "call_id": str(call.id),
            "provider": call.provider,
            "provider_call_id": call.provider_call_id,
            "status": call.status,
            "started_at": call.started_at.isoformat() if call.started_at else None,
            "dispatch": dispatch,
        }
