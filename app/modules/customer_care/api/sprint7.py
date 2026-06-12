"""Sprint 7 routes — channel expansion (Module 47).

Adds 5 channels: email, SMS, Messenger, Instagram DM, web-chat widget.

Inbound webhook routes (all public — secrets / signatures vary per provider):
- GET  /webhooks/messenger          — Meta verify-challenge
- POST /webhooks/messenger          — FB Messenger inbound
- GET  /webhooks/instagram          — Meta verify-challenge
- POST /webhooks/instagram          — Instagram DM inbound
- POST /webhooks/email              — generic email inbound (Postmark/Mailgun/SendGrid shape)
- POST /webhooks/sms                — generic SMS inbound (BulkSMSBD / Twilio shape)

Web-chat widget (public — origin allow-list):
- POST /webchat/init                — open a session, get session_id
- POST /webchat/{sid}/messages      — customer sends message
- GET  /webchat/{sid}/poll          — customer polls for pending agent replies
- GET  /webchat/widget.js           — minimal JS snippet the storefront embeds

Agent-side outbound trigger (authenticated):
- POST /conversations/{id}/send-via-channel — explicit send through a non-default channel
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import EmailStr, Field
from sqlalchemy import desc, select, text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.customer_care import channels, service, sse_bus
from app.modules.customer_care.config import settings as cc_settings
from app.modules.customer_care.models import (
    CCConversation,
    CCMessage,
    CCWebhookIdempotency,
)

_AGENT = "customercare.agent"

router = APIRouter(tags=["customer-care-sprint7"])
_log = get_logger("hypershop.customer_care.sprint7")


# ============================================================== Shared helpers
async def _resolve_or_create_by_phone(session, phone: str, default_name: str | None) -> UUID:
    return await service.resolve_or_create_customer_by_phone(
        session=session, phone=phone, default_name=default_name,
    )


async def _resolve_or_create_by_email(
    session, email: str, default_name: str | None = None,
) -> UUID:
    """Same shape as the phone path but for email-only customers."""
    row = (
        await session.execute(
            _t("SELECT id FROM users WHERE email = :e"),
            {"e": email},
        )
    ).first()
    if row:
        return row[0]
    ins = await session.execute(
        _t(
            """
            INSERT INTO users (
                id, email, full_name, password_hash,
                status, failed_login_count, created_at, updated_at
            ) VALUES (
                gen_random_uuid(), :e, :n, '!ghost!email!', 'active', 0, now(), now()
            )
            RETURNING id
            """,
        ),
        {"e": email, "n": default_name or f"Email customer {email}"},
    )
    new_id = ins.scalar_one()
    # Ensure cc_customer_profile exists
    await session.execute(
        _t(
            "INSERT INTO cc_customer_profile (customer_id) VALUES (:c) "
            "ON CONFLICT (customer_id) DO NOTHING"
        ),
        {"c": new_id},
    )
    return new_id


async def _ingest_inbound_message(
    session,
    *,
    customer_id: UUID,
    channel: str,
    body: str | None,
    external_msg_id: str,
    source_label: str | None = None,
) -> CCConversation:
    """Find/create open conversation + idempotent message insert."""
    # Idempotency
    exists = (
        await session.execute(
            select(CCWebhookIdempotency).where(
                CCWebhookIdempotency.channel == channel,
                CCWebhookIdempotency.channel_message_id == external_msg_id,
            )
        )
    ).scalar_one_or_none()
    if exists:
        return None  # type: ignore[return-value]
    session.add(CCWebhookIdempotency(channel=channel, channel_message_id=external_msg_id))
    # Conv
    conv = (
        await session.execute(
            select(CCConversation)
            .where(CCConversation.customer_id == customer_id)
            .where(CCConversation.status == "open")
            .where(CCConversation.channel == channel)
            .order_by(desc(CCConversation.last_message_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if conv is None:
        conv = CCConversation(
            customer_id=customer_id,
            channel=channel,
            source=source_label or channel,
            status="open",
        )
        session.add(conv)
        await session.flush()
        agent_id = await service.choose_agent(session)
        if agent_id:
            await service.assign_agent(session, conversation=conv, agent_id=agent_id)
    await service.append_message(
        session, conversation=conv, sender_type="customer",
        body=body, channel=channel, whatsapp_message_id=external_msg_id,
    )
    sse_bus.publish(
        {
            "type": "message.received", "conversation_id": str(conv.id),
            "channel": channel, "preview": (body or "")[:80],
        },
        agent_id=conv.agent_id,
    )
    return conv


# ============================================================== MESSENGER WEBHOOK
@router.get(
    "/webhooks/messenger",
    summary="Meta page subscribe — verify-challenge handshake",
)
async def messenger_verify(
    mode: str = Query(default="", alias="hub.mode"),
    token: str = Query(default="", alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
):
    cfg = cc_settings()
    if mode == "subscribe" and token == cfg.messenger_verify_token:
        return int(challenge) if challenge.isdigit() else challenge
    raise HTTPException(status_code=403, detail="invalid verify token")


@router.post(
    "/webhooks/messenger",
    summary="Facebook Messenger inbound webhook",
)
async def messenger_inbound(
    payload: Annotated[dict, Body(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    entries = payload.get("entry") or []
    ingested = 0
    for entry in entries:
        for evt in entry.get("messaging") or []:
            psid = ((evt.get("sender") or {}).get("id"))
            msg = evt.get("message") or {}
            mid = msg.get("mid")
            text = msg.get("text") or ""
            if not psid or not mid:
                continue
            # Use the PSID as a synthetic phone-like id for the user
            synth_phone = f"messenger:{psid}"
            async with uow.transactional() as session:
                customer_id = await _resolve_or_create_by_phone(
                    session, phone=synth_phone, default_name=f"Messenger user {psid[:8]}",
                )
                conv = await _ingest_inbound_message(
                    session, customer_id=customer_id, channel="messenger",
                    body=text, external_msg_id=mid,
                )
                if conv is not None:
                    ingested += 1
    return {"received": True, "messages_ingested": ingested}


# ============================================================== INSTAGRAM WEBHOOK
@router.get(
    "/webhooks/instagram",
    summary="Instagram messaging — verify-challenge handshake",
)
async def instagram_verify(
    mode: str = Query(default="", alias="hub.mode"),
    token: str = Query(default="", alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
):
    cfg = cc_settings()
    if mode == "subscribe" and token == cfg.instagram_verify_token:
        return int(challenge) if challenge.isdigit() else challenge
    raise HTTPException(status_code=403, detail="invalid verify token")


@router.post(
    "/webhooks/instagram",
    summary="Instagram DM inbound webhook",
)
async def instagram_inbound(
    payload: Annotated[dict, Body(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    entries = payload.get("entry") or []
    ingested = 0
    for entry in entries:
        for evt in entry.get("messaging") or []:
            ig_user_id = ((evt.get("sender") or {}).get("id"))
            msg = evt.get("message") or {}
            mid = msg.get("mid")
            text = msg.get("text") or ""
            if not ig_user_id or not mid:
                continue
            synth_phone = f"instagram:{ig_user_id}"
            async with uow.transactional() as session:
                customer_id = await _resolve_or_create_by_phone(
                    session, phone=synth_phone, default_name=f"Instagram user {ig_user_id[:8]}",
                )
                conv = await _ingest_inbound_message(
                    session, customer_id=customer_id, channel="instagram",
                    body=text, external_msg_id=mid,
                )
                if conv is not None:
                    ingested += 1
    return {"received": True, "messages_ingested": ingested}


# ============================================================== EMAIL WEBHOOK
class EmailWebhookPayload(StrictModel):
    """Generic inbound-email shape. Operators map their provider's
    JSON keys to these via a small upstream transform (or use the
    raw POST below).
    """
    from_address: EmailStr
    from_name: str | None = None
    subject: str = Field(default="", max_length=500)
    body_plain: str = Field(default="", max_length=200_000)
    message_id: str = Field(..., min_length=1, max_length=255)


def _verify_shared_secret(provided: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


@router.post(
    "/webhooks/email",
    summary="Generic inbound email webhook (Postmark/Mailgun/SendGrid-shape)",
)
async def email_inbound(
    body: EmailWebhookPayload,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    x_email_secret: Annotated[str | None, Header(alias="X-CC-Inbound-Secret")] = None,
) -> dict[str, Any]:
    cfg = cc_settings()
    if not _verify_shared_secret(x_email_secret, cfg.email_inbound_secret):
        raise HTTPException(status_code=403, detail="invalid_secret")
    async with uow.transactional() as session:
        customer_id = await _resolve_or_create_by_email(
            session, email=body.from_address, default_name=body.from_name,
        )
        # Use subject + body as the message; prefix subject so the
        # agent inbox shows what the thread is about.
        text = (
            f"[Subject: {body.subject}]\n\n{body.body_plain}"
            if body.subject else body.body_plain
        )
        conv = await _ingest_inbound_message(
            session, customer_id=customer_id, channel="email",
            body=text[:8000], external_msg_id=body.message_id,
            source_label="email",
        )
        ingested = 1 if conv is not None else 0
    return {"received": True, "messages_ingested": ingested}


# ============================================================== SMS WEBHOOK
class SmsWebhookPayload(StrictModel):
    from_phone: str = Field(..., min_length=6, max_length=32)
    body: str = Field(..., min_length=1, max_length=2000)
    message_id: str = Field(..., min_length=1, max_length=255)
    sender_name: str | None = None


@router.post(
    "/webhooks/sms",
    summary="Generic inbound SMS webhook (BulkSMSBD / Twilio normalised)",
)
async def sms_inbound(
    body: SmsWebhookPayload,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    x_sms_secret: Annotated[str | None, Header(alias="X-CC-Inbound-Secret")] = None,
) -> dict[str, Any]:
    cfg = cc_settings()
    if not _verify_shared_secret(x_sms_secret, cfg.sms_inbound_secret):
        raise HTTPException(status_code=403, detail="invalid_secret")
    async with uow.transactional() as session:
        customer_id = await _resolve_or_create_by_phone(
            session, phone=body.from_phone, default_name=body.sender_name,
        )
        conv = await _ingest_inbound_message(
            session, customer_id=customer_id, channel="sms",
            body=body.body, external_msg_id=body.message_id,
            source_label="sms",
        )
        ingested = 1 if conv is not None else 0
    return {"received": True, "messages_ingested": ingested}


# ============================================================== WEBCHAT
class WebchatInitRequest(StrictModel):
    customer_name: str | None = Field(default=None, max_length=120)
    customer_email: EmailStr | None = None


class WebchatSendRequest(StrictModel):
    body: str = Field(..., min_length=1, max_length=4096)


def _check_webchat_origin(request: Request) -> None:
    """Origin allow-list enforcement. Strict in prod; lenient in dev."""
    cfg = cc_settings()
    if not cfg.is_production:
        return
    allowed = {o.strip() for o in (cfg.webchat_allowed_origins or "").split(",") if o.strip()}
    origin = request.headers.get("origin", "")
    if origin not in allowed:
        raise HTTPException(status_code=403, detail={"error": "origin_not_allowed", "origin": origin})


@router.post(
    "/webchat/init",
    summary="Open a web-chat session — returns session_id the customer uses to send + poll",
)
async def webchat_init(
    request: Request,
    body: WebchatInitRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    _check_webchat_origin(request)
    # 12 URL-safe bytes ≈ 16 chars → "webchat:<16>" stays under
    # the users.phone varchar(32) limit.
    session_id = secrets.token_urlsafe(12)
    synth_phone = f"webchat:{session_id}"
    async with uow.transactional() as s:
        if body.customer_email:
            customer_id = await _resolve_or_create_by_email(
                s, email=body.customer_email, default_name=body.customer_name,
            )
        else:
            customer_id = await _resolve_or_create_by_phone(
                s, phone=synth_phone, default_name=body.customer_name,
            )
    return {
        "session_id": session_id,
        "customer_id": str(customer_id),
        "poll_interval_seconds": 3,
    }


@router.post(
    "/webchat/{session_id}/messages",
    summary="Customer sends a message via the web-chat widget",
)
async def webchat_send(
    session_id: Annotated[str, Path(..., min_length=8, max_length=64)],
    body: WebchatSendRequest,
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    _check_webchat_origin(request)
    synth_phone = f"webchat:{session_id}"
    msg_id = f"webchat-{session_id}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    async with uow.transactional() as s:
        # The customer must have been provisioned via /webchat/init.
        # We re-resolve to keep this endpoint idempotent.
        customer_id = await _resolve_or_create_by_phone(
            s, phone=synth_phone, default_name=None,
        )
        conv = await _ingest_inbound_message(
            s, customer_id=customer_id, channel="webchat",
            body=body.body, external_msg_id=msg_id, source_label="webchat",
        )
    return {"received": True, "session_id": session_id, "duplicated": conv is None}


@router.get(
    "/webchat/{session_id}/poll",
    summary="Customer polls for pending agent replies (drains a per-session queue)",
)
async def webchat_poll(
    session_id: Annotated[str, Path(..., min_length=8, max_length=64)],
    request: Request,
) -> dict[str, Any]:
    _check_webchat_origin(request)
    msgs = channels.webchat_drain(session_id)
    return {"messages": msgs, "session_id": session_id}


@router.get(
    "/webchat/widget.js",
    summary="Minimal embeddable widget — drop a <script> tag in any storefront page",
    response_class=PlainTextResponse,
)
async def webchat_widget_js() -> str:
    """Returns vanilla-JS widget code. Storefront integrates with:

    ``<script src="https://api.hypershop.com.bd/api/v1/customer-care/webchat/widget.js"></script>``

    Default visual is a 60×60 chat-bubble at bottom-right. Customer
    types, hits send → POST /webchat/{sid}/messages. Polling every
    3s for replies.
    """
    cfg = cc_settings()
    base = cfg.base_url.rstrip("/")
    return (
        "(function(){\n"
        f"var BASE='{base}/api/v1/customer-care';\n"
        "var sessionId=null;\n"
        "function mkEl(t,s){var e=document.createElement(t);if(s)e.style.cssText=s;return e;}\n"
        "var bubble=mkEl('div','position:fixed;bottom:20px;right:20px;width:60px;height:60px;"
        "border-radius:50%;background:#2f6feb;color:#fff;display:flex;align-items:center;"
        "justify-content:center;cursor:pointer;font-size:28px;z-index:99999;box-shadow:0 4px 12px rgba(0,0,0,0.2);');\n"
        "bubble.innerHTML='💬';document.body.appendChild(bubble);\n"
        "var panel=mkEl('div','position:fixed;bottom:90px;right:20px;width:320px;height:420px;"
        "background:#fff;border:1px solid #ddd;border-radius:12px;display:none;flex-direction:column;"
        "overflow:hidden;z-index:99999;box-shadow:0 8px 24px rgba(0,0,0,0.15);');\n"
        "var hdr=mkEl('div','background:#2f6feb;color:#fff;padding:10px 14px;font:600 14px sans-serif;');\n"
        "hdr.textContent='Hypershop · Customer Care';panel.appendChild(hdr);\n"
        "var msgs=mkEl('div','flex:1;overflow-y:auto;padding:10px;font:14px sans-serif;background:#f8f9fa;');\n"
        "panel.appendChild(msgs);\n"
        "var inp=mkEl('input','border:0;border-top:1px solid #ddd;padding:12px;font:14px sans-serif;outline:none;');\n"
        "inp.placeholder='Type a message…';panel.appendChild(inp);\n"
        "document.body.appendChild(panel);\n"
        "function add(role,text){var d=mkEl('div','margin:6px 0;padding:8px 10px;border-radius:8px;"
        "max-width:80%;'+(role==='customer'?'margin-left:auto;background:#2f6feb;color:#fff;':"
        "'background:#fff;border:1px solid #ddd;color:#222;'));d.textContent=text;msgs.appendChild(d);"
        "msgs.scrollTop=msgs.scrollHeight;}\n"
        "function init(){return fetch(BASE+'/webchat/init',{method:'POST',headers:{'Content-Type':"
        "'application/json'},body:JSON.stringify({})}).then(function(r){return r.json();}).then("
        "function(j){sessionId=j.session_id;});}\n"
        "function poll(){if(!sessionId)return;fetch(BASE+'/webchat/'+sessionId+'/poll').then("
        "function(r){return r.json();}).then(function(j){(j.messages||[]).forEach(function(m){"
        "add('agent',m.body||'');});});}\n"
        "bubble.onclick=function(){panel.style.display=panel.style.display==='flex'?'none':'flex';"
        "if(!sessionId)init();};\n"
        "inp.onkeydown=function(e){if(e.key==='Enter'&&inp.value.trim()){var t=inp.value;inp.value='';"
        "add('customer',t);if(!sessionId)return;fetch(BASE+'/webchat/'+sessionId+'/messages',{"
        "method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body:t})});}};\n"
        "setInterval(poll,3000);\n"
        "})();\n"
    )


# ============================================================== Agent outbound dispatch
class SendViaChannelRequest(StrictModel):
    body: str = Field(..., min_length=1, max_length=4096)
    # If the conv has multiple channels available, force one
    force_channel: str | None = Field(default=None, max_length=20)


@router.post(
    "/conversations/{conv_id}/send-via-channel",
    summary="Send a message through the conversation's channel — routes to the right adapter",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def send_via_channel(
    conv_id: Annotated[UUID, Path(...)],
    body: SendViaChannelRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        conv = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise NotFoundError("Conversation not found")
        target_channel = body.force_channel or conv.channel
        # Pull customer phone/email for the right outbound key
        r = (
            await session.execute(
                _t("SELECT email::text, phone FROM users WHERE id = :uid"),
                {"uid": conv.customer_id},
            )
        ).first()
        email, phone = (r[0], r[1]) if r else (None, None)
        # Persist the message first
        msg = CCMessage(
            conversation_id=conv.id, sender_type="agent",
            message_type="text", message_body=body.body,
            channel=target_channel,
        )
        session.add(msg)
        await session.flush()
        await record_audit(
            actor=principal,
            action=f"customer_care.message.sent.{target_channel}",
            resource_type="cc_messages", resource_id=msg.id,
        )
    # Dispatch outside the txn
    ok = False
    reason = "no_handler"
    if target_channel == "email" and email:
        ok = await channels.send_email(
            to_address=email, subject="Re: Hypershop support", body_text=body.body,
        )
        reason = "email_smtp" if ok else "email_failed_or_no_creds"
    elif target_channel == "sms" and phone:
        result = await channels.send_sms(to_phone=phone, body=body.body)
        ok = result is not None
        reason = "sms_sent" if ok else "sms_failed_or_no_creds"
    elif target_channel == "messenger" and phone and phone.startswith("messenger:"):
        psid = phone.split(":", 1)[1]
        result = await channels.send_messenger(to_psid=psid, body=body.body)
        ok = result is not None
        reason = "messenger_sent" if ok else "messenger_failed_or_no_creds"
    elif target_channel == "instagram" and phone and phone.startswith("instagram:"):
        ig_id = phone.split(":", 1)[1]
        result = await channels.send_instagram_dm(to_ig_user_id=ig_id, body=body.body)
        ok = result is not None
        reason = "instagram_sent" if ok else "instagram_failed_or_no_creds"
    elif target_channel == "webchat" and phone and phone.startswith("webchat:"):
        sid = phone.split(":", 1)[1]
        channels.webchat_push(sid, {"body": body.body, "from": "agent"})
        ok = True
        reason = "webchat_pushed"
    else:
        # Fallback to WhatsApp text via existing helper
        from app.modules.customer_care import outbound
        if phone:
            r = await outbound.send_whatsapp_text(to_phone=phone, body=body.body)
            ok = r is not None
            reason = "whatsapp_fallback"
    return {
        "ok": ok, "channel": target_channel, "reason": reason,
        "message_id": str(msg.id),
    }
