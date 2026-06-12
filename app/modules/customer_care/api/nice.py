"""Nice-to-have routes — v7 additions.

- GET  /integrations/status          — real config check (was stubbed false)
- GET  /reports/agent-performance    — per-agent KPIs over a period
- GET  /reports/sla                  — SLA breach rollup
- GET  /admin/tiles                  — JSON for the main Hypershop admin panel
- POST /sheets/sync/conversations    — Google Sheets export (rolling 30d)
- POST /conversations/{id}/buttons   — send interactive button message
- POST /conversations/{id}/list      — send interactive list message
- POST /conversations/{id}/typing    — push typing indicator to customer
- POST /webhooks/voice/transcribe    — internal: transcribe queued voice note
- GET  /channels                     — list available channels + status
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import Field
from sqlalchemy import desc, select, text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.customer_care import outbound, sse_bus
from app.modules.customer_care.config import settings as cc_settings
from app.modules.customer_care.models import (
    CCAgentProfile,
    CCConversation,
    CCMessage,
)

_AGENT = "customercare.agent"
_ADMIN = "customercare.admin"

router = APIRouter(tags=["customer-care-nice"])
_log = get_logger("hypershop.customer_care.nice")


# ============================================================== Integrations status
class IntegrationConn(StrictModel):
    connected: bool
    detail: str | None = None


class IntegrationsStatus(StrictModel):
    whatsapp_cloud: IntegrationConn
    whatsapp_signature_verification: IntegrationConn
    openai_chat: IntegrationConn
    openai_embeddings: IntegrationConn
    google_sheets: IntegrationConn
    templates: dict[str, bool]
    public_base_url: str


@router.get(
    "/integrations/status",
    response_model=IntegrationsStatus,
    summary="Real configuration status of every external service CC depends on",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def get_integrations_status() -> IntegrationsStatus:
    cfg = cc_settings()
    return IntegrationsStatus(
        whatsapp_cloud=IntegrationConn(
            connected=bool(cfg.whatsapp_access_token and cfg.whatsapp_phone_number_id),
            detail=("send + receive" if cfg.whatsapp_access_token else "log-only"),
        ),
        whatsapp_signature_verification=IntegrationConn(
            connected=bool(cfg.whatsapp_app_secret),
            detail="HMAC enforced" if cfg.whatsapp_app_secret else "skipped (no app secret)",
        ),
        openai_chat=IntegrationConn(
            connected=bool(cfg.openai_api_key),
            detail=cfg.openai_model if cfg.openai_api_key else "AI auto-reply disabled",
        ),
        openai_embeddings=IntegrationConn(
            connected=bool(cfg.openai_api_key),
            detail=cfg.openai_embedding_model if cfg.openai_api_key else "LIKE-only KB search",
        ),
        google_sheets=IntegrationConn(
            connected=bool(cfg.google_sheets_client_email and cfg.google_sheets_private_key),
        ),
        templates={
            "order_paid": bool(cfg.template_order_paid),
            "order_dispatched": bool(cfg.template_order_dispatched),
            "order_delivered": bool(cfg.template_order_delivered),
            "payment_success": bool(cfg.template_payment_success),
        },
        public_base_url=cfg.base_url,
    )


# ============================================================== Reports
class AgentPerfRow(StrictModel):
    agent_id: UUID
    agent_email: str | None
    agent_name: str | None
    messages_sent: int
    conversations_handled: int
    conversations_resolved: int
    avg_first_response_seconds: float | None
    csat_avg: float | None
    csat_count: int


class SLAReport(StrictModel):
    window_days: int
    total_open: int
    first_response_breached: int
    resolution_breached: int
    avg_first_response_seconds: float | None
    avg_resolution_seconds: float | None


@router.get(
    "/reports/agent-performance",
    response_model=list[AgentPerfRow],
    summary="Per-agent KPIs over a rolling window",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def report_agent_performance(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=1, le=365),
) -> list[AgentPerfRow]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    WITH msg_counts AS (
                        SELECT
                            c.agent_id,
                            COUNT(*) FILTER (WHERE m.sender_type = 'agent') AS sent,
                            COUNT(DISTINCT c.id) AS convs
                        FROM cc_messages m
                        JOIN cc_conversations c ON c.id = m.conversation_id
                        WHERE m.created_at >= now() - INTERVAL '{int(days)} days'
                          AND c.agent_id IS NOT NULL
                        GROUP BY c.agent_id
                    ),
                    resolved AS (
                        SELECT agent_id, COUNT(*) AS resolved_count
                        FROM cc_conversations
                        WHERE resolved_at >= now() - INTERVAL '{int(days)} days'
                          AND agent_id IS NOT NULL
                        GROUP BY agent_id
                    ),
                    frt AS (
                        SELECT
                            agent_id,
                            AVG(EXTRACT(EPOCH FROM (first_response_at - created_at))) AS avg_secs
                        FROM cc_conversations
                        WHERE first_response_at IS NOT NULL
                          AND created_at >= now() - INTERVAL '{int(days)} days'
                          AND agent_id IS NOT NULL
                        GROUP BY agent_id
                    ),
                    csat AS (
                        SELECT agent_id, AVG(score)::float AS avg_score, COUNT(*) AS n
                        FROM cc_csat_surveys
                        WHERE responded_at IS NOT NULL
                          AND responded_at >= now() - INTERVAL '{int(days)} days'
                          AND agent_id IS NOT NULL
                        GROUP BY agent_id
                    )
                    SELECT
                        u.id, u.email::text, u.full_name,
                        COALESCE(mc.sent, 0), COALESCE(mc.convs, 0),
                        COALESCE(r.resolved_count, 0),
                        f.avg_secs, c.avg_score, COALESCE(c.n, 0)
                    FROM cc_agent_profile a
                    JOIN users u ON u.id = a.user_id
                    LEFT JOIN msg_counts mc ON mc.agent_id = a.user_id
                    LEFT JOIN resolved r   ON r.agent_id  = a.user_id
                    LEFT JOIN frt f        ON f.agent_id  = a.user_id
                    LEFT JOIN csat c       ON c.agent_id  = a.user_id
                    ORDER BY COALESCE(mc.sent, 0) DESC
                    """,
                )
            )
        ).all()
        return [
            AgentPerfRow(
                agent_id=r[0], agent_email=r[1], agent_name=r[2],
                messages_sent=int(r[3]), conversations_handled=int(r[4]),
                conversations_resolved=int(r[5]),
                avg_first_response_seconds=float(r[6]) if r[6] is not None else None,
                csat_avg=float(r[7]) if r[7] is not None else None,
                csat_count=int(r[8]),
            )
            for r in rows
        ]


@router.get(
    "/reports/sla",
    response_model=SLAReport,
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def report_sla(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=1, le=365),
) -> SLAReport:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t(
                    f"""
                    SELECT
                        (SELECT COUNT(*) FROM cc_conversations WHERE status = 'open') AS open,
                        (SELECT COUNT(*) FROM cc_conversations
                         WHERE sla_first_response_breached = true
                           AND created_at >= now() - INTERVAL '{int(days)} days') AS fr_breach,
                        (SELECT COUNT(*) FROM cc_conversations
                         WHERE sla_resolution_breached = true
                           AND created_at >= now() - INTERVAL '{int(days)} days') AS res_breach,
                        (SELECT AVG(EXTRACT(EPOCH FROM (first_response_at - created_at)))
                         FROM cc_conversations
                         WHERE first_response_at IS NOT NULL
                           AND created_at >= now() - INTERVAL '{int(days)} days') AS avg_frt,
                        (SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - created_at)))
                         FROM cc_conversations
                         WHERE resolved_at IS NOT NULL
                           AND created_at >= now() - INTERVAL '{int(days)} days') AS avg_resolution
                    """,
                )
            )
        ).first()
        return SLAReport(
            window_days=days,
            total_open=int(r[0] or 0),
            first_response_breached=int(r[1] or 0),
            resolution_breached=int(r[2] or 0),
            avg_first_response_seconds=float(r[3]) if r[3] is not None else None,
            avg_resolution_seconds=float(r[4]) if r[4] is not None else None,
        )


# ============================================================== Admin tiles
class AdminTiles(StrictModel):
    """Compact JSON for the Hypershop admin-panel dashboard. Returns
    a handful of CC KPIs without requiring the admin to navigate to
    the /customercare PWA."""
    open_conversations: int
    unassigned_conversations: int
    sla_breached: int
    handover_required: int
    online_agents: int
    total_agents: int
    csat_avg_7d: float | None
    csat_avg_30d: float | None
    kb_documents: int
    dlq_pending: int


@router.get(
    "/admin/tiles",
    response_model=AdminTiles,
    summary="Compact CC KPIs for the main admin-panel dashboard",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def get_admin_tiles(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdminTiles:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM cc_conversations WHERE status = 'open') AS open,
                        (SELECT COUNT(*) FROM cc_conversations
                         WHERE status = 'open' AND agent_id IS NULL) AS unassigned,
                        (SELECT COUNT(*) FROM cc_conversations
                         WHERE status = 'open'
                           AND (sla_first_response_breached OR sla_resolution_breached)) AS breached,
                        (SELECT COUNT(*) FROM cc_conversations
                         WHERE handover_required = true AND status = 'open') AS handover,
                        (SELECT COUNT(*) FROM cc_agent_profile WHERE status = 'online') AS online_a,
                        (SELECT COUNT(*) FROM cc_agent_profile) AS total_a,
                        (SELECT AVG(score)::float FROM cc_csat_surveys
                         WHERE responded_at IS NOT NULL
                           AND responded_at >= now() - INTERVAL '7 days') AS csat7,
                        (SELECT AVG(score)::float FROM cc_csat_surveys
                         WHERE responded_at IS NOT NULL
                           AND responded_at >= now() - INTERVAL '30 days') AS csat30,
                        (SELECT COUNT(*) FROM cc_knowledge_documents WHERE is_active) AS kb,
                        (SELECT COUNT(*) FROM cc_dead_letters WHERE status = 'pending') AS dlq
                    """,
                )
            )
        ).first()
        return AdminTiles(
            open_conversations=int(r[0] or 0),
            unassigned_conversations=int(r[1] or 0),
            sla_breached=int(r[2] or 0),
            handover_required=int(r[3] or 0),
            online_agents=int(r[4] or 0),
            total_agents=int(r[5] or 0),
            csat_avg_7d=float(r[6]) if r[6] is not None else None,
            csat_avg_30d=float(r[7]) if r[7] is not None else None,
            kb_documents=int(r[8] or 0),
            dlq_pending=int(r[9] or 0),
        )


# ============================================================== Channels
class ChannelStatus(StrictModel):
    name: str
    connected: bool
    inbound_supported: bool
    outbound_supported: bool
    detail: str | None = None


@router.get(
    "/providers/bd-status",
    summary="Bangladeshi-first SMS/voice provider connection status",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def bd_provider_status() -> dict:
    """SIM-gateway (local BD SIM) / SSL Wireless / BulkSMSBD / Twilio state.
    Drives the Voice AI Agent page's provider board. Pure config read."""
    from app.modules.customer_care.channels import bd_providers_status

    return bd_providers_status()


@router.get(
    "/channels",
    response_model=list[ChannelStatus],
    summary="List supported messaging channels and their connection state",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def list_channels() -> list[ChannelStatus]:
    """Updated in sprint 7 with real outbound + inbound status per channel."""
    cfg = cc_settings()
    wa_out = bool(cfg.whatsapp_access_token and cfg.whatsapp_phone_number_id)
    wa_sig = bool(cfg.whatsapp_app_secret)
    sms_out = bool(
        cfg.bulksms_bd_api_token
        or (cfg.twilio_account_sid and cfg.twilio_auth_token and cfg.twilio_from_number)
    )
    sms_in = bool(cfg.sms_inbound_secret)
    email_out = bool(cfg.smtp_host and cfg.smtp_from_address)
    email_in = bool(cfg.email_inbound_secret)
    msgr = bool(cfg.messenger_page_access_token and cfg.messenger_page_id)
    ig = bool(cfg.instagram_page_access_token and cfg.instagram_account_id)
    return [
        ChannelStatus(
            name="whatsapp", connected=wa_out,
            inbound_supported=True, outbound_supported=True,
            detail=(
                f"Meta Cloud API · signature_verify={'on' if wa_sig else 'off'}"
                if wa_out else "log-only"
            ),
        ),
        ChannelStatus(
            name="sms",
            connected=sms_out or sms_in,
            inbound_supported=sms_in,
            outbound_supported=sms_out,
            detail=(
                "BulkSMSBD" if cfg.bulksms_bd_api_token
                else ("Twilio" if cfg.twilio_account_sid else "no provider")
            ),
        ),
        ChannelStatus(
            name="email",
            connected=email_out or email_in,
            inbound_supported=email_in,
            outbound_supported=email_out,
            detail=f"SMTP={cfg.smtp_host or 'unset'} · inbound_secret={'set' if email_in else 'unset'}",
        ),
        ChannelStatus(
            name="webchat", connected=True,
            inbound_supported=True, outbound_supported=True,
            detail="In-process — widget at /api/v1/customer-care/webchat/widget.js",
        ),
        ChannelStatus(
            name="messenger",
            connected=msgr,
            inbound_supported=True, outbound_supported=msgr,
            detail="Meta Graph (FB page)" if msgr else "creds not configured",
        ),
        ChannelStatus(
            name="instagram",
            connected=ig,
            inbound_supported=True, outbound_supported=ig,
            detail="Meta Graph (IG business)" if ig else "creds not configured",
        ),
    ]


# ============================================================== Interactive
class InteractiveButtonsRequest(StrictModel):
    body: str = Field(..., min_length=1, max_length=1024)
    buttons: list[dict[str, str]] = Field(..., min_length=1, max_length=3)
    header: str | None = Field(default=None, max_length=60)
    footer: str | None = Field(default=None, max_length=60)


class InteractiveListRequest(StrictModel):
    body: str = Field(..., min_length=1, max_length=1024)
    button_text: str = Field(default="Choose", max_length=20)
    sections: list[dict[str, Any]] = Field(..., min_length=1, max_length=10)
    header: str | None = Field(default=None, max_length=60)
    footer: str | None = Field(default=None, max_length=60)


@router.post(
    "/conversations/{conv_id}/buttons",
    summary="Send a WhatsApp interactive button message (up to 3 buttons)",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def send_buttons(
    conv_id: Annotated[UUID, Path(...)],
    body: InteractiveButtonsRequest,
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
        # Pull phone
        from sqlalchemy import text as _tx
        r = (
            await session.execute(
                _tx("SELECT phone FROM users WHERE id = :uid"),
                {"uid": conv.customer_id},
            )
        ).first()
        phone = r[0] if r else None
        # Persist as a message so the agent sees it in the thread
        msg = CCMessage(
            conversation_id=conv.id, sender_type="agent",
            message_type="interactive_buttons",
            message_body=body.body,
            media_url=json.dumps({"buttons": body.buttons, "header": body.header}),
            channel=conv.channel,
        )
        session.add(msg)
        await record_audit(
            actor=principal,
            action="customer_care.interactive.buttons_sent",
            resource_type="cc_messages", resource_id=msg.id,
        )
    if phone:
        await outbound.send_whatsapp_interactive_buttons(
            to_phone=phone, body=body.body, buttons=body.buttons,
            header=body.header, footer=body.footer,
        )
    return {"sent": True, "kind": "buttons"}


@router.post(
    "/conversations/{conv_id}/list",
    summary="Send a WhatsApp interactive list message",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def send_list(
    conv_id: Annotated[UUID, Path(...)],
    body: InteractiveListRequest,
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
        from sqlalchemy import text as _tx
        r = (
            await session.execute(
                _tx("SELECT phone FROM users WHERE id = :uid"),
                {"uid": conv.customer_id},
            )
        ).first()
        phone = r[0] if r else None
        msg = CCMessage(
            conversation_id=conv.id, sender_type="agent",
            message_type="interactive_list",
            message_body=body.body,
            media_url=json.dumps({"sections": body.sections, "button_text": body.button_text}),
            channel=conv.channel,
        )
        session.add(msg)
        await record_audit(
            actor=principal,
            action="customer_care.interactive.list_sent",
            resource_type="cc_messages", resource_id=msg.id,
        )
    if phone:
        await outbound.send_whatsapp_interactive_list(
            to_phone=phone, body=body.body,
            button_text=body.button_text, sections=body.sections,
            header=body.header, footer=body.footer,
        )
    return {"sent": True, "kind": "list"}


# ============================================================== Typing indicator
@router.post(
    "/conversations/{conv_id}/typing",
    summary="Push a 'typing...' indicator to the customer + broadcast SSE event",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def push_typing_indicator(
    conv_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    """Agent UI calls this when the agent starts typing. Sends a Meta
    typing indicator to the customer AND publishes an SSE event so
    other co-watching agents see it too.
    """
    async with uow.transactional() as session:
        conv = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise NotFoundError("Conversation not found")
        from sqlalchemy import text as _tx
        r = (
            await session.execute(
                _tx("SELECT phone FROM users WHERE id = :uid"),
                {"uid": conv.customer_id},
            )
        ).first()
        phone = r[0] if r else None
    if phone:
        await outbound.send_whatsapp_typing_indicator(to_phone=phone)
    sse_bus.publish({
        "type": "agent.typing",
        "conversation_id": str(conv_id),
        "agent_id": str(principal.user_id),
    })
    return {"pushed": True}


# ============================================================== Google Sheets export
class SheetsSyncRequest(StrictModel):
    spreadsheet_id: str | None = None  # default to env config
    tab: str = Field(default="Conversations", max_length=80)
    days: int = Field(default=30, ge=1, le=365)


@router.post(
    "/sheets/sync/conversations",
    summary="Append recent conversations to a Google Sheet (rolling window)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def sheets_sync_conversations(
    body: SheetsSyncRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    """Pulls every conversation from the last N days + appends to a
    Google Sheet via the GoogleSheetsClient (which reads service
    account creds from env). When creds are missing we degrade to
    a CSV-text response so the admin can copy/paste manually.
    """
    cfg = cc_settings()
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT
                        c.id::text, c.created_at, c.status,
                        u.phone, u.full_name,
                        COALESCE(c.last_message, ''),
                        c.handover_required, c.priority,
                        c.sla_first_response_breached, c.sla_resolution_breached
                    FROM cc_conversations c
                    JOIN users u ON u.id = c.customer_id
                    WHERE c.created_at >= now() - INTERVAL '{int(body.days)} days'
                    ORDER BY c.created_at DESC
                    """,
                )
            )
        ).all()
        formatted_rows = [
            [
                str(r[0]), r[1].isoformat() if r[1] else "", r[2] or "",
                r[3] or "", r[4] or "", (r[5] or "")[:200],
                "Y" if r[6] else "", r[7] or "",
                "Y" if r[8] else "", "Y" if r[9] else "",
            ]
            for r in rows
        ]
        await record_audit(
            actor=principal,
            action="customer_care.sheets.synced",
            resource_type="cc_conversations",
            metadata={"row_count": len(formatted_rows), "days": body.days},
        )
    sheet_id = body.spreadsheet_id or cfg.google_sheets_spreadsheet_id
    if not (cfg.google_sheets_client_email and cfg.google_sheets_private_key and sheet_id):
        # Degraded mode — return CSV in the response so admin sees the data
        header = "id,created_at,status,phone,name,last_message,handover,priority,fr_breach,res_breach"
        csv_lines = [header] + [
            ",".join('"' + (c or "").replace('"', '""') + '"' for c in row)
            for row in formatted_rows
        ]
        return {
            "ok": False,
            "reason": "google_sheets_not_configured",
            "row_count": len(formatted_rows),
            "csv_preview": "\n".join(csv_lines[:50]),
        }
    # Real Google Sheets append — implementation lives in CC's
    # integrations.py (GoogleSheetsClient.append_row). We do row-by-row
    # to keep retry granular.
    from app.modules.customer_care.integrations import sheets_client
    client = sheets_client()
    pushed = 0
    failed = 0
    for row in formatted_rows:
        try:
            client.append_row(sheet_id, row, tab=body.tab)
            pushed += 1
        except Exception as e:  # noqa: BLE001
            _log.warning("sheets_append_failed", error=str(e))
            failed += 1
    return {
        "ok": True, "row_count": len(formatted_rows),
        "pushed": pushed, "failed": failed,
        "spreadsheet_id": sheet_id, "tab": body.tab,
    }


# ============================================================== Tracking link
def order_tracking_url(order_code: str, base_url: str | None = None) -> str:
    """Public-facing order tracking URL. The customer-web app should
    expose /track?code=... to render this; for now we just emit the
    URL pattern."""
    cfg = cc_settings()
    base = (base_url or cfg.base_url).rstrip("/")
    return f"{base}/track?code={order_code}"
