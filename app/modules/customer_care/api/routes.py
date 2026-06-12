from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.channels import parse_whatsapp_payload
from app.config import settings
from app.csat import csat_summary, start_csat, submit_csat
from app.db import get_db
from app.dlq import write_dlq
from app.integrations import openai_client, sheets_client, whatsapp_client
from app.models import (
    CheckoutEvent,
    Conversation,
    CSATSurvey,
    Customer,
    DEFAULT_TENANT_ID,
    DeadLetterEntry,
    Followup,
    GdprDeletionRequest,
    KnowledgeChunk,
    KnowledgeDocument,
    Message,
    Order,
    PaymentEvent,
    Product,
    User,
)
from app.observability import metrics_endpoint
from app.security import (
    admin_user,
    current_user,
    dashboard_user,
    decode_token,
    hash_password,
    is_revoked,
    issue_token_pair,
    revoke_jti,
    token_for,
    verify_password,
)
from app.services import QUEUE_NAMES, QUEUES, enqueue, receive_incoming
from app.sla import scan_breaches
from app.sse import stream_for_agent
from app.webhook_signature import verify_meta_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

UuidStr = Annotated[str, Path(min_length=36, max_length=36, pattern=r"^[0-9a-fA-F-]{36}$")]


def _validate_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID") from exc


# ── schemas ────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


class AgentIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=200)
    name: str | None = None
    role: str = "agent"
    max_active_chats: int = Field(default=300, ge=1, le=10_000)


class StatusIn(BaseModel):
    status: str = Field(min_length=1, max_length=40)


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    category: str | None = None
    price: Decimal
    stock: int = 0
    description: str | None = None
    image_url: str | None = None
    offer_text: str | None = None
    is_active: bool = True


class CustomerIn(BaseModel):
    phone: str = Field(min_length=4, max_length=40)
    name: str | None = None
    full_address: str | None = None
    location_link: str | None = None


class SendIn(BaseModel):
    message_body: str = Field(min_length=1, max_length=4096)
    media_url: str | None = None


class AssignIn(BaseModel):
    agent_id: UUID


class OrderIn(BaseModel):
    customer_id: UUID
    product_id: UUID | None = None
    product_name: str | None = None
    quantity: int = Field(default=1, ge=1, le=10_000)
    customer_name: str | None = None
    customer_phone: str | None = None
    full_address: str | None = None
    location_link: str | None = None


class ConfirmIn(BaseModel):
    order_id: UUID
    confirmation_text: str = Field(min_length=1, max_length=80)


class CampaignIn(BaseModel):
    campaign_name: str = Field(min_length=1, max_length=160)
    customer_ids: list[UUID]
    product_id: UUID | None = None


class CSATIn(BaseModel):
    token: str = Field(min_length=8, max_length=120)
    score: int = Field(ge=1, le=5)
    comment: str | None = None


class GdprDeleteIn(BaseModel):
    customer_phone: str | None = None
    customer_id: UUID | None = None
    reason: str | None = None


def obj(model) -> dict:
    return {c.name: getattr(model, c.name) for c in model.__table__.columns}


# ── auth ───────────────────────────────────────────────────────────────

@router.post("/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    tokens = issue_token_pair(user)
    return {
        **tokens,
        "must_change_password": user.must_change_password,
        "user": obj(user),
    }


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return obj(user)


@router.post("/auth/refresh")
def refresh(payload: RefreshIn):
    decoded = decode_token(payload.refresh_token, expected_type="refresh")
    if is_revoked(decoded.get("jti", "")):
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    fake = User(
        id=decoded["sub"],
        role=decoded["role"],
        must_change_password=decoded.get("must_change_password", False),
        username="token",
        password_hash="",
    )
    return {"access_token": token_for(fake), "token_type": "bearer"}


@router.post("/auth/logout")
def logout(payload: LogoutIn | None = None):
    if payload and payload.refresh_token:
        try:
            decoded = decode_token(payload.refresh_token, expected_type="refresh")
            revoke_jti(decoded.get("jti", ""))
        except HTTPException:
            pass
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(
    payload: PasswordIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
    return {"ok": True}


# ── agents ─────────────────────────────────────────────────────────────

@router.get("/agents")
def agents(_: User = Depends(dashboard_user), db: Session = Depends(get_db)):
    return [
        obj(a)
        for a in db.scalars(select(User).where(User.role == "agent").order_by(User.username)).all()
    ]


@router.post("/agents")
def create_agent(
    payload: AgentIn,
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="Username exists")
    user = User(
        username=payload.username,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        max_active_chats=payload.max_active_chats,
        must_change_password=True,
    )
    db.add(user)
    db.commit()
    return obj(user)


@router.patch("/agents/{agent_id}/status")
def agent_status(
    agent_id: UuidStr,
    payload: StatusIn,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    agent = db.get(User, _validate_uuid(agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.status = payload.status
    if payload.status == "offline":
        agent.current_active_chats = 0
    db.commit()
    return obj(agent)


# ── customers ──────────────────────────────────────────────────────────

@router.get("/customers")
def customers(_: User = Depends(dashboard_user), db: Session = Depends(get_db)):
    return [
        obj(c)
        for c in db.scalars(
            select(Customer)
            .where(Customer.deleted_at.is_(None))
            .order_by(Customer.updated_at.desc())
            .limit(500)
        ).all()
    ]


@router.post("/customers")
def upsert_customer(
    payload: CustomerIn,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    customer = db.scalar(select(Customer).where(Customer.phone == payload.phone)) or Customer(
        phone=payload.phone
    )
    customer.name = payload.name
    customer.full_address = payload.full_address
    customer.location_link = payload.location_link
    db.add(customer)
    db.commit()
    return obj(customer)


# ── conversations ──────────────────────────────────────────────────────

@router.get("/conversations")
def conversations(_: User = Depends(dashboard_user), db: Session = Depends(get_db)):
    return [
        obj(c)
        for c in db.scalars(
            select(Conversation).order_by(Conversation.last_message_at.desc()).limit(500)
        ).all()
    ]


@router.get("/conversations/{conversation_id}")
def conversation(
    conversation_id: UuidStr,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    item = db.get(Conversation, _validate_uuid(conversation_id))
    if not item:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return obj(item)


@router.get("/conversations/{conversation_id}/messages")
def messages(
    conversation_id: UuidStr,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    cid = _validate_uuid(conversation_id)
    return [
        obj(m)
        for m in db.scalars(
            select(Message)
            .where(Message.conversation_id == cid)
            .order_by(Message.created_at)
        ).all()
    ]


@router.post("/conversations/{conversation_id}/send")
def send_message(
    conversation_id: UuidStr,
    payload: SendIn,
    user: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    cid = _validate_uuid(conversation_id)
    convo = db.get(Conversation, cid)
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msg = Message(
        tenant_id=convo.tenant_id,
        conversation_id=cid,
        sender_type="agent",
        message_body=payload.message_body,
        media_url=payload.media_url,
    )
    convo.last_message = payload.message_body
    convo.last_message_at = datetime.utcnow()
    if convo.first_response_at is None:
        convo.first_response_at = datetime.utcnow()
    db.add(msg)
    db.commit()
    enqueue("whatsapp-send-queue", {"conversation_id": cid, "agent_id": user.id})
    return obj(msg)


@router.patch("/conversations/{conversation_id}/assign")
@router.patch("/conversations/{conversation_id}/transfer")
def assign(
    conversation_id: UuidStr,
    payload: AssignIn,
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    convo = db.get(Conversation, _validate_uuid(conversation_id))
    agent = db.get(User, str(payload.agent_id))
    if not convo or not agent:
        raise HTTPException(status_code=404, detail="Conversation or agent not found")
    convo.agent_id = agent.id
    convo.status = "open"
    db.commit()
    return obj(convo)


@router.patch("/conversations/{conversation_id}/resolve")
def resolve(
    conversation_id: UuidStr,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    convo = db.get(Conversation, _validate_uuid(conversation_id))
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    convo.status = "resolved"
    convo.resolved_at = datetime.utcnow()
    db.commit()
    enqueue("csat-send-queue", {"conversation_id": convo.id})
    return obj(convo)


@router.patch("/conversations/{conversation_id}/handover")
def handover(
    conversation_id: UuidStr,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    convo = db.get(Conversation, _validate_uuid(conversation_id))
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    convo.handover_required = True
    convo.handover_reason = "manual"
    db.commit()
    return obj(convo)


# ── products ───────────────────────────────────────────────────────────

@router.get("/products")
def products(_: User = Depends(dashboard_user), db: Session = Depends(get_db)):
    return [
        obj(p)
        for p in db.scalars(select(Product).order_by(Product.name).limit(500)).all()
    ]


@router.post("/products")
def create_product(
    payload: ProductIn,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    product = Product(**payload.model_dump())
    db.add(product)
    db.commit()
    return obj(product)


@router.get("/products/search")
def search_products(
    q: str = Query(min_length=1, max_length=120),
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    like = f"%{q}%"
    return [
        obj(p)
        for p in db.scalars(
            select(Product)
            .where(
                Product.is_active.is_(True),
                or_(
                    Product.name.ilike(like),
                    Product.sku.ilike(like),
                    Product.category.ilike(like),
                ),
            )
            .limit(25)
        ).all()
    ]


@router.patch("/products/{product_id}")
def update_product(
    product_id: UuidStr,
    payload: ProductIn,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    product = db.get(Product, _validate_uuid(product_id))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    for key, value in payload.model_dump().items():
        setattr(product, key, value)
    db.commit()
    return obj(product)


# ── orders ─────────────────────────────────────────────────────────────

@router.post("/orders/draft")
def draft_order(
    payload: OrderIn,
    user: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    product = db.get(Product, str(payload.product_id)) if payload.product_id else None
    price = product.price if product else None
    order = Order(
        order_code=f"ORD-{int(datetime.utcnow().timestamp())}-{uuid.uuid4().hex[:6]}",
        customer_id=str(payload.customer_id),
        agent_id=user.id,
        product_id=str(payload.product_id) if payload.product_id else None,
        product_name=product.name if product else payload.product_name,
        quantity=payload.quantity,
        price=price,
        total_amount=(price * payload.quantity if price else None),
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        full_address=payload.full_address,
        location_link=payload.location_link,
        status="waiting_confirmation",
    )
    db.add(order)
    db.commit()
    enqueue("order-confirmation-queue", {"order_id": order.id})
    return obj(order)


@router.post("/orders/confirm")
def confirm_order(
    payload: ConfirmIn,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    order = db.get(Order, str(payload.order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if payload.confirmation_text.strip().lower() not in {"yes", "confirm", "confirmed", "কনফার্ম"}:
        raise HTTPException(status_code=400, detail="Final confirmation required")
    order.status = "confirmed"
    order.confirmed_at = datetime.utcnow()
    db.commit()
    enqueue("google-sheet-sync-queue", {"kind": "order", "order_id": order.id})
    return obj(order)


@router.get("/orders")
def orders(_: User = Depends(dashboard_user), db: Session = Depends(get_db)):
    return [
        obj(o)
        for o in db.scalars(select(Order).order_by(Order.created_at.desc()).limit(500)).all()
    ]


@router.patch("/orders/{order_id}/status")
def order_status(
    order_id: UuidStr,
    payload: StatusIn,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    order = db.get(Order, _validate_uuid(order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = payload.status
    db.commit()
    return obj(order)


# ── follow-ups ─────────────────────────────────────────────────────────

@router.post("/followups/campaign")
def campaign(
    payload: CampaignIn,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    count = 0
    for cid in payload.customer_ids:
        customer = db.get(Customer, str(cid))
        if customer and customer.consent_status != "stopped":
            db.add(
                Followup(
                    customer_id=str(cid),
                    product_id=str(payload.product_id) if payload.product_id else None,
                    campaign_name=payload.campaign_name,
                )
            )
            count += 1
    db.commit()
    enqueue("followup-message-queue", {"campaign": payload.campaign_name})
    return {"created": count}


@router.get("/followups")
def followups(_: User = Depends(dashboard_user), db: Session = Depends(get_db)):
    return [
        obj(f)
        for f in db.scalars(
            select(Followup).order_by(Followup.created_at.desc()).limit(500)
        ).all()
    ]


@router.patch("/followups/{followup_id}/status")
def followup_status(
    followup_id: UuidStr,
    payload: StatusIn,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    followup = db.get(Followup, _validate_uuid(followup_id))
    if not followup:
        raise HTTPException(status_code=404, detail="Followup not found")
    followup.status = payload.status
    db.commit()
    return obj(followup)


@router.post("/followups/{followup_id}/send-now")
def followup_send(
    followup_id: UuidStr,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    followup = db.get(Followup, _validate_uuid(followup_id))
    if not followup:
        raise HTTPException(status_code=404, detail="Followup not found")
    followup.status = "sent"
    followup.last_sent_at = datetime.utcnow()
    db.commit()
    enqueue("followup-message-queue", {"followup_id": followup_id})
    return obj(followup)


# ── WhatsApp webhook (signed + idempotent) ────────────────────────────

@router.get("/whatsapp/webhook")
def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    cfg = settings()
    if not cfg.whatsapp_verify_token:
        raise HTTPException(status_code=503, detail="Verify token not configured")
    if hub_mode == "subscribe" and hub_verify_token == cfg.whatsapp_verify_token:
        try:
            return int(hub_challenge or "0")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid challenge")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    cfg = settings()
    body = await verify_meta_signature(request, cfg.whatsapp_app_secret)

    try:
        import json
        payload = json.loads(body) if body else {}
    except Exception as exc:
        write_dlq(
            db,
            source="whatsapp_webhook",
            operation="parse_body",
            payload=body,
            error=exc,
            request_id=request.headers.get("x-request-id"),
        )
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    enqueue("incoming-message-queue", payload)

    incoming = parse_whatsapp_payload(payload)
    processed = 0
    for msg in incoming:
        try:
            convo = receive_incoming(db, msg, tenant_id=cfg.default_tenant_id)
            if convo is not None:
                processed += 1
        except Exception as exc:
            logger.exception(
                "whatsapp_webhook_message_failed channel_message_id=%s",
                msg.get("channel_message_id"),
            )
            write_dlq(
                db,
                source="whatsapp_webhook",
                operation="receive_incoming",
                payload=msg,
                error=exc,
                request_id=request.headers.get("x-request-id"),
            )
    return {"ok": True, "processed": processed}


# ── outbound channel helpers ──────────────────────────────────────────

@router.post("/whatsapp/send-text")
def send_text(payload: dict, _: User = Depends(dashboard_user)):
    enqueue("whatsapp-send-queue", payload)
    return {"queued": True, "dry_run": not bool(settings().whatsapp_access_token)}


@router.post("/whatsapp/send-image")
@router.post("/whatsapp/send-template")
def send_media(payload: dict, _: User = Depends(dashboard_user)):
    enqueue("whatsapp-send-queue", payload)
    return {"queued": True}


@router.post("/sheets/sync-order")
@router.post("/sheets/sync-customer")
@router.post("/sheets/sync-followup")
@router.post("/sheets/sync-agent-report")
def sheets_sync(payload: dict, _: User = Depends(dashboard_user)):
    enqueue("google-sheet-sync-queue", payload)
    return {"queued": True, "dry_run": True}


# ── reports ───────────────────────────────────────────────────────────

@router.get("/queues")
def queues(_: User = Depends(admin_user)):
    return {"queues": QUEUE_NAMES, "depths": {name: len(QUEUES[name]) for name in QUEUE_NAMES}}


@router.get("/reports/summary")
def report_summary(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return {
        "agents": db.scalar(select(func.count(User.id)).where(User.role == "agent")) or 0,
        "customers": db.scalar(select(func.count(Customer.id))) or 0,
        "active_conversations": db.scalar(
            select(func.count(Conversation.id)).where(Conversation.status == "open")
        )
        or 0,
        "pending_conversations": db.scalar(
            select(func.count(Conversation.id)).where(Conversation.status == "pending")
        )
        or 0,
        "confirmed_orders": db.scalar(
            select(func.count(Order.id)).where(Order.status == "confirmed")
        )
        or 0,
        "sales_amount": float(
            db.scalar(
                select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                    Order.status == "confirmed"
                )
            )
            or 0
        ),
    }


@router.get("/reports/agent-performance")
def agent_perf(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [
        {"agent": a.username, "active_chats": a.current_active_chats, "status": a.status}
        for a in db.scalars(select(User).where(User.role == "agent")).all()
    ]


@router.get("/reports/csat")
def report_csat(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return csat_summary(db)


@router.get("/reports/sla")
def report_sla(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    breached_fr = db.scalar(
        select(func.count(Conversation.id)).where(Conversation.sla_first_response_breached.is_(True))
    ) or 0
    breached_res = db.scalar(
        select(func.count(Conversation.id)).where(Conversation.sla_resolution_breached.is_(True))
    ) or 0
    total = db.scalar(select(func.count(Conversation.id))) or 0
    return {
        "total_conversations": int(total),
        "first_response_breaches": int(breached_fr),
        "resolution_breaches": int(breached_res),
    }


@router.get("/integrations/status")
def integration_status(_: User = Depends(admin_user)):
    cfg = settings()
    return {
        "require_external_integrations": cfg.require_external_integrations,
        "whatsapp": {
            "connected": whatsapp_client().enabled,
            "has_verify_token": bool(cfg.whatsapp_verify_token),
            "has_access_token": bool(cfg.whatsapp_access_token),
            "has_phone_number_id": bool(cfg.whatsapp_phone_number_id),
            "has_app_secret": bool(cfg.whatsapp_app_secret),
        },
        "openai": {"connected": openai_client().enabled, "model": cfg.openai_model},
        "google_sheets": {
            "connected": sheets_client().enabled,
            "order_sheet": bool(cfg.google_sheets_order_sheet_id),
            "customer_sheet": bool(cfg.google_sheets_customer_sheet_id),
            "followup_sheet": bool(cfg.google_sheets_followup_sheet_id),
            "agent_report_sheet": bool(cfg.google_sheets_agent_report_sheet_id),
        },
        "observability": {
            "sentry": bool(cfg.sentry_dsn),
            "otel": bool(cfg.otel_exporter_otlp_endpoint),
        },
        "pii_encryption": bool(cfg.pii_encryption_keys_json and cfg.pii_active_kid),
    }


# ── CSAT ──────────────────────────────────────────────────────────────

@router.post("/conversations/{conversation_id}/csat/start")
def csat_start(
    conversation_id: UuidStr,
    _: User = Depends(dashboard_user),
    db: Session = Depends(get_db),
):
    survey = start_csat(db, _validate_uuid(conversation_id))
    return {
        "id": survey.id,
        "token": survey.survey_token,
        "conversation_id": survey.conversation_id,
    }


@router.post("/csat/submit")
def csat_submit(payload: CSATIn, db: Session = Depends(get_db)):
    try:
        survey = submit_csat(db, token=payload.token, score=payload.score, comment=payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": survey.id, "score": survey.score}


# ── SLA ───────────────────────────────────────────────────────────────

@router.post("/sla/scan")
def sla_scan_now(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return scan_breaches(db)


# ── DLQ ───────────────────────────────────────────────────────────────

@router.get("/dlq")
def dlq_list(
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
    status: str | None = Query(default="pending", max_length=40),
):
    rows = db.scalars(
        select(DeadLetterEntry)
        .where(DeadLetterEntry.status == status if status else True)
        .order_by(DeadLetterEntry.created_at.desc())
        .limit(200)
    ).all()
    return [
        {
            "id": r.id,
            "source": r.source,
            "operation": r.operation,
            "error": r.error_class,
            "message": r.error_message,
            "created_at": r.created_at,
            "attempts": r.attempts,
            "status": r.status,
        }
        for r in rows
    ]


# ── GDPR ──────────────────────────────────────────────────────────────

@router.post("/gdpr/delete-my-data")
def gdpr_delete(
    payload: GdprDeleteIn,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    if not (payload.customer_phone or payload.customer_id):
        raise HTTPException(status_code=400, detail="customer_phone or customer_id required")
    customer = None
    if payload.customer_id:
        customer = db.get(Customer, str(payload.customer_id))
    elif payload.customer_phone:
        customer = db.scalar(select(Customer).where(Customer.phone == payload.customer_phone))
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    request_row = GdprDeletionRequest(
        tenant_id=customer.tenant_id,
        customer_id=customer.id,
        customer_phone=customer.phone,
        requested_by=user.id,
        reason=payload.reason,
        status="processing",
    )
    db.add(request_row)
    db.flush()

    customer.name = None
    customer.full_address = None
    customer.location_link = None
    customer.phone = f"deleted-{uuid.uuid4().hex[:12]}"
    customer.consent_status = "deleted"
    customer.status = "deleted"
    customer.deleted_at = datetime.utcnow()

    db.execute(
        Message.__table__.update()
        .where(Message.conversation_id.in_(
            select(Conversation.id).where(Conversation.customer_id == customer.id)
        ))
        .values(message_body=None, media_url=None)
    )

    request_row.status = "completed"
    request_row.completed_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "deletion_request_id": request_row.id}


# ── SSE inbox ─────────────────────────────────────────────────────────

@router.get("/inbox/stream")
async def inbox_stream(
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """SSE inbox stream.

    EventSource cannot set the `Authorization` header, so this endpoint also
    accepts the access token via the `?token=` query string (SSE-only fallback).
    The token still goes through the full kid-class-aware verifier and
    refresh-blacklist check, so it's no weaker than a normal Bearer header.
    """
    user: User | None = None
    auth_header = request.headers.get("authorization", "")
    bearer = (
        auth_header.split(" ", 1)[1]
        if auth_header.lower().startswith("bearer ")
        else None
    )
    candidate = bearer or token
    if not candidate:
        raise HTTPException(status_code=401, detail="Missing access token")

    payload = decode_token(candidate, expected_type="access")
    if is_revoked(payload.get("jti", "")):
        raise HTTPException(status_code=401, detail="Token revoked")
    user = db.get(User, payload.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")

    return StreamingResponse(
        stream_for_agent(user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── KB (RAG knowledge base) ───────────────────────────────────────────

class KbDocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=200_000)
    source_type: str = Field(default="text", max_length=40)
    source_url: str | None = None
    language: str | None = Field(default=None, max_length=8)


@router.post("/kb/documents")
async def kb_create_document(
    payload: KbDocumentIn,
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    from app.rag.ingest import ingest_text

    try:
        doc = await ingest_text(
            db,
            title=payload.title,
            body=payload.body,
            source_type=payload.source_type,
            source_url=payload.source_url,
            language=payload.language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return obj(doc)


@router.get("/kb/documents")
def kb_list_documents(
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(KnowledgeDocument)
        .order_by(KnowledgeDocument.created_at.desc())
        .limit(500)
    ).all()
    return [
        {
            "id": d.id,
            "title": d.title,
            "source_type": d.source_type,
            "language": d.language,
            "chunk_count": d.chunk_count,
            "embedding_model": d.embedding_model,
            "is_active": d.is_active,
            "indexed_at": d.indexed_at,
            "created_at": d.created_at,
        }
        for d in rows
    ]


@router.delete("/kb/documents/{document_id}")
def kb_delete_document(
    document_id: UuidStr,
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    from app.rag.ingest import delete_document

    if not delete_document(db, _validate_uuid(document_id)):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}


@router.get("/kb/search")
async def kb_search(
    q: str = Query(min_length=1, max_length=500),
    k: int = Query(default=5, ge=1, le=20),
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Debug/admin search endpoint — runs the same RAG retrieval the AI uses."""
    from app.rag.retrieval import retrieve

    chunks = await retrieve(db, q, k=k)
    return [
        {
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "position": c.position,
            "score": round(c.score, 3),
            "text": c.text,
        }
        for c in chunks
    ]


@router.get("/kb/stats")
def kb_stats(
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    return {
        "documents": db.scalar(select(func.count(KnowledgeDocument.id))) or 0,
        "active_documents": db.scalar(
            select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.is_active.is_(True))
        )
        or 0,
        "chunks": db.scalar(select(func.count(KnowledgeChunk.id))) or 0,
    }


# ── External webhooks (checkout / payment / whatsapp alias) ──────────

# Convention used by /api/webhooks/* endpoints:
#   X-Webhook-Signature: sha256=<hex over `<timestamp>.<body>`>
#   X-Webhook-Timestamp: <unix-seconds>
# Each endpoint uses a separate secret env var so a leak of one doesn't
# compromise the others. Production refuses missing-secret config.

@router.post("/webhooks/checkout")
async def webhook_checkout(request: Request, db: Session = Depends(get_db)):
    from app.webhooks.handlers import ingest_checkout_event
    from app.webhooks.signature import verify_webhook_signature

    cfg = settings()
    body = await verify_webhook_signature(request, cfg.checkout_webhook_secret)
    try:
        import json as _json
        payload = _json.loads(body) if body else {}
    except Exception as exc:
        write_dlq(
            db,
            source="checkout_webhook",
            operation="parse_body",
            payload=body,
            error=exc,
            request_id=request.headers.get("x-request-id"),
        )
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    try:
        row, status = ingest_checkout_event(db, payload, raw_body=body)
    except Exception as exc:
        logger.exception("checkout_ingest_failed")
        write_dlq(
            db,
            source="checkout_webhook",
            operation="ingest",
            payload=payload,
            error=exc,
            request_id=request.headers.get("x-request-id"),
        )
        return {"ok": False, "error": "ingest_failed"}
    if status == "duplicate":
        return {"ok": True, "duplicate": True}
    if status == "invalid" or row is None:
        return {"ok": False, "error": "missing_required_fields"}
    return {"ok": True, "event_id": row.event_id, "id": row.id}


@router.post("/webhooks/payment")
async def webhook_payment(request: Request, db: Session = Depends(get_db)):
    from app.webhooks.handlers import ingest_payment_event
    from app.webhooks.signature import verify_webhook_signature

    cfg = settings()
    body = await verify_webhook_signature(request, cfg.payment_webhook_secret)
    try:
        import json as _json
        payload = _json.loads(body) if body else {}
    except Exception as exc:
        write_dlq(
            db,
            source="payment_webhook",
            operation="parse_body",
            payload=body,
            error=exc,
            request_id=request.headers.get("x-request-id"),
        )
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    try:
        row, status = ingest_payment_event(db, payload, raw_body=body)
    except Exception as exc:
        logger.exception("payment_ingest_failed")
        write_dlq(
            db,
            source="payment_webhook",
            operation="ingest",
            payload=payload,
            error=exc,
            request_id=request.headers.get("x-request-id"),
        )
        return {"ok": False, "error": "ingest_failed"}
    if status == "duplicate":
        return {"ok": True, "duplicate": True}
    if status == "invalid" or row is None:
        return {"ok": False, "error": "missing_required_fields"}
    return {"ok": True, "event_id": row.event_id, "id": row.id}


@router.get("/webhooks/checkout/recent")
def webhooks_recent_checkout(
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
):
    rows = db.scalars(
        select(CheckoutEvent).order_by(CheckoutEvent.received_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "provider": r.provider,
            "event_id": r.event_id,
            "event_type": r.event_type,
            "order_id": r.order_id,
            "customer_phone": r.customer_phone,
            "processed": r.processed,
            "received_at": r.received_at,
        }
        for r in rows
    ]


@router.get("/webhooks/payment/recent")
def webhooks_recent_payment(
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
):
    rows = db.scalars(
        select(PaymentEvent).order_by(PaymentEvent.received_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "provider": r.provider,
            "event_id": r.event_id,
            "event_type": r.event_type,
            "order_id": r.order_id,
            "customer_phone": r.customer_phone,
            "amount": float(r.amount) if r.amount is not None else None,
            "currency": r.currency,
            "processed": r.processed,
            "received_at": r.received_at,
        }
        for r in rows
    ]


# Alias: industry-standard URL pattern for the WhatsApp webhook.
# Both /api/whatsapp/webhook (legacy) and /api/webhooks/whatsapp (alias) work.
@router.post("/webhooks/whatsapp")
async def whatsapp_webhook_alias(request: Request, db: Session = Depends(get_db)):
    return await whatsapp_webhook(request, db)


@router.get("/webhooks/whatsapp")
def whatsapp_webhook_alias_verify(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    return verify_webhook(hub_mode, hub_verify_token, hub_challenge)


# ── metrics (no auth — scrape from internal network only) ─────────────

@router.get("/metrics", include_in_schema=False)
def metrics():
    return metrics_endpoint()
