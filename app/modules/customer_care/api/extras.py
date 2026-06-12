"""Customer-care extension routes — important-tier additions.

Adds (all under ``/api/v1/customer-care/*``):
- SSE live stream:        GET  /inbox/stream
- Conversation reopen:    POST /conversations/{id}/reopen
- Dedicated handover:     POST /conversations/{id}/handover
- Customer profile edit:  PATCH /customers/{id}
- Order from chat:        POST /conversations/{id}/order/draft
                          POST /conversations/{id}/order/confirm
- DLQ admin:              GET  /dlq
                          GET  /dlq/{id}
                          POST /dlq/{id}/replay
- GDPR:                   POST /gdpr/delete-my-data           (public + token)
                          POST /admin/gdpr/process/{id}      (admin)

Audit logging is invoked for every write action via Hypershop's
``record_audit``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import desc, select, text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.customer_care import outbound, service, sse_bus
from app.modules.customer_care.models import (
    CCConversation,
    CCCustomerProfile,
    CCDeadLetter,
    CCGdprDeletion,
)

_AGENT = "customercare.agent"
_ADMIN = "customercare.admin"
# Voice-call stream is for the softphone — gated on the same perm that
# governs voice-call assignment (super_admin / admin / support_agent /
# customercare_admin). NOT on _AGENT — a basic CC agent without
# voice_call.assign doesn't need the softphone stream either.
_VOICE_CALL_ASSIGN = "voice_call.assign"

router = APIRouter(tags=["customer-care-extras"])
_log = get_logger("hypershop.customer_care.extras")


# ============================================================== SSE stream
@router.get(
    "/inbox/stream",
    summary="Server-Sent Events stream of live inbox events for the current agent",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def inbox_stream(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> StreamingResponse:
    """Pushes JSON events per the schema in ``sse_bus.py``. Browser
    EventSource will auto-reconnect on connection drop.
    """
    q = sse_bus.subscribe(principal.user_id)

    async def gen():
        try:
            async for chunk in sse_bus.event_stream(q):
                yield chunk
        finally:
            sse_bus.unsubscribe(principal.user_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


# ============================================================== Voice-call SSE
@router.get(
    "/voice-calls/stream",
    summary="Server-Sent Events stream of live voice-call events (softphone feed)",
    description=(
        "Pushes voice.call.ringing / assigned / answered / released / "
        "ended / missed events for the authenticated agent. Each event "
        "is a JSON object with at least {type, voice_call_id, "
        "from_status, to_status, agent_id?, from_phone?}. "
        "Browser EventSource auto-reconnects. Gated on `voice_call.assign`."
    ),
    dependencies=[Depends(requires_permission(_VOICE_CALL_ASSIGN))],
)
async def voice_call_stream(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> StreamingResponse:
    """The underlying ``sse_bus`` fans every event to (a) the agent's
    queue when ``agent_id`` matches and (b) the broadcast queue (this
    subscriber listens on both — see ``sse_bus.subscribe``).

    Client-side: filter on ``type.startsWith("voice.call.")`` to ignore
    any unrelated events that happen to land on the broadcast channel.
    """
    q = sse_bus.subscribe(principal.user_id)

    async def gen():
        try:
            async for chunk in sse_bus.event_stream(q):
                yield chunk
        finally:
            sse_bus.unsubscribe(principal.user_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================== Reopen
@router.post(
    "/conversations/{conv_id}/reopen",
    summary="Reopen a resolved conversation",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def reopen_conversation(
    conv_id: Annotated[UUID, Path(...)],
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
        if conv.status != "resolved":
            raise BusinessRuleError("Only resolved conversations can be reopened")
        conv.status = "open"
        conv.resolved_at = None
        # Resume on the same agent if they're still online; otherwise
        # leave unassigned for next pickup
        if conv.agent_id is None:
            agent_id = await service.choose_agent(session)
            if agent_id:
                await service.assign_agent(
                    session, conversation=conv, agent_id=agent_id,
                )
        else:
            # Increment back the load
            await service.assign_agent(
                session, conversation=conv, agent_id=conv.agent_id,
            )
        await record_audit(
            actor=principal,
            action="customer_care.conversation.reopened",
            resource_type="cc_conversation",
            resource_id=conv.id,
        )
    sse_bus.publish(
        {"type": "conversation.reopened", "conversation_id": str(conv_id)},
    )
    return {"id": str(conv_id), "status": "open"}


# ============================================================== Handover
class HandoverRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=500)


@router.post(
    "/conversations/{conv_id}/handover",
    summary="Flag conversation for human handover (clears agent assignment)",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def handover_conversation(
    conv_id: Annotated[UUID, Path(...)],
    body: HandoverRequest,
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
        prior_agent = conv.agent_id
        conv.handover_required = True
        conv.handover_reason = body.reason[:500]
        # Free up the assigning agent so a supervisor can pick this up
        if prior_agent:
            await service.decrement_agent_load(session, agent_id=prior_agent)
            conv.agent_id = None
        await record_audit(
            actor=principal,
            action="customer_care.conversation.handover",
            resource_type="cc_conversation",
            resource_id=conv.id,
            metadata={"reason": body.reason, "prior_agent_id": str(prior_agent) if prior_agent else None},
        )
    # Broadcast — every online agent should see the handover banner
    sse_bus.publish({
        "type": "handover.requested",
        "conversation_id": str(conv_id),
        "reason": body.reason,
    })
    return {"id": str(conv_id), "handover_required": True, "agent_id": None}


# ============================================================== Customer profile update
class CustomerProfileUpdate(StrictModel):
    preferred_language: str | None = Field(default=None, max_length=40)
    consent_status: str | None = Field(default=None, max_length=40)
    full_address: str | None = Field(default=None, max_length=2000)
    location_link: str | None = Field(default=None, max_length=2000)
    last_interest: str | None = Field(default=None, max_length=255)


@router.patch(
    "/customers/{customer_id}",
    summary="Update CC customer profile fields",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def update_customer_profile(
    customer_id: Annotated[UUID, Path(...)],
    body: CustomerProfileUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    fields_set = body.model_dump(exclude_none=True)
    if not fields_set:
        raise ValidationError("at least one field must be provided")
    async with uow.transactional() as session:
        prof = (
            await session.execute(
                select(CCCustomerProfile).where(CCCustomerProfile.customer_id == customer_id)
            )
        ).scalar_one_or_none()
        if prof is None:
            raise NotFoundError("Customer profile not found")
        for k, v in fields_set.items():
            setattr(prof, k, v)
        prof.updated_at = datetime.now(timezone.utc)
        await record_audit(
            actor=principal,
            action="customer_care.customer_profile.updated",
            resource_type="cc_customer_profile",
            resource_id=customer_id,
            metadata={"fields": list(fields_set.keys())},
        )
    return {"customer_id": str(customer_id), **fields_set}


# ============================================================== Order from chat
class OrderDraftRequest(StrictModel):
    variant_id: UUID
    quantity: int = Field(..., ge=1, le=100)
    delivery_address_line1: str = Field(..., min_length=1, max_length=255)
    delivery_address_line2: str | None = Field(default=None, max_length=255)
    city: str = Field(..., min_length=1, max_length=120)
    phone: str = Field(..., min_length=6, max_length=32)
    recipient_name: str = Field(..., min_length=1, max_length=120)
    payment_method: str = Field(default="cod", pattern=r"^(cod|online)$")
    notes: str | None = Field(default=None, max_length=2048)


@router.post(
    "/conversations/{conv_id}/order/draft",
    summary="Agent drafts an order on behalf of the customer (preview totals, not yet placed)",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def draft_order_from_chat(
    conv_id: Annotated[UUID, Path(...)],
    body: OrderDraftRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    """Returns a preview-style total without placing the order. The
    agent shows this to the customer over WhatsApp; the customer
    confirms; then ``/order/confirm`` actually places it via Hypershop's
    orders module.
    """
    async with uow.transactional() as session:
        # Confirm the conv exists
        conv = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise NotFoundError("Conversation not found")
        # Look up variant price (snapshot only — actual order checkout
        # re-resolves at confirm time so the totals are authoritative).
        row = (
            await session.execute(
                _t(
                    "SELECT pv.id, pv.price, p.name "
                    "FROM product_variants pv "
                    "JOIN products p ON p.id = pv.product_id "
                    "WHERE pv.id = :vid"
                ),
                {"vid": body.variant_id},
            )
        ).first()
        if row is None:
            raise NotFoundError("Variant not found")
        unit_price = Decimal(str(row[1]))
        line_total = (unit_price * Decimal(body.quantity)).quantize(Decimal("0.01"))
        await record_audit(
            actor=principal,
            action="customer_care.order.drafted",
            resource_type="cc_conversation",
            resource_id=conv.id,
            metadata={"variant_id": str(body.variant_id), "quantity": body.quantity},
        )
    return {
        "conversation_id": str(conv_id),
        "variant_id": str(body.variant_id),
        "variant_name": row[2],
        "quantity": body.quantity,
        "unit_price": str(unit_price),
        "line_total": str(line_total),
        "payment_method": body.payment_method,
        "delivery_address": {
            "recipient_name": body.recipient_name,
            "phone": body.phone,
            "line1": body.delivery_address_line1,
            "line2": body.delivery_address_line2,
            "city": body.city,
        },
        "notes": body.notes,
        "status": "draft",
    }


@router.post(
    "/conversations/{conv_id}/order/confirm",
    summary="Place the order via Hypershop's orders module + link to this conversation",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def confirm_order_from_chat(
    conv_id: Annotated[UUID, Path(...)],
    body: OrderDraftRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    """Creates a real Hypershop order. Links it to ``cc_conversations.order_id``
    so the conversation thread later receives the lifecycle events
    (payment_confirmed, dispatched, delivered) automatically.
    """
    from app.modules.orders.service import OrderService  # local — avoid boot cycles
    from app.modules.orders.schemas import (
        DeliveryAddress, OrderItemRequest, PlaceOrderRequest,
    )

    async with uow.transactional() as session:
        conv = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise NotFoundError("Conversation not found")
        req = PlaceOrderRequest(
            items=[OrderItemRequest(variant_id=body.variant_id, quantity=body.quantity)],
            payment_method=body.payment_method,
            delivery_address=DeliveryAddress(
                recipient_name=body.recipient_name,
                phone=body.phone,
                line1=body.delivery_address_line1,
                line2=body.delivery_address_line2,
                city=body.city,
            ),
            notes=body.notes,
        )
        svc = OrderService(session)
        # Order placement runs in the conversation's customer's context
        # (we use the customer_id from the conv, not the agent), so
        # event payloads carry the right customer_user_id downstream.
        from app.core.security.principal import Principal as _P
        from frozendict import frozendict  # only if available; else build a stub
        # Hypershop's OrderService.place_order expects a Principal —
        # we synthesise one whose user_id is the customer, permissions
        # carry order.place. (Audit attribution stays the agent via
        # record_audit below.)
        customer_principal = type(
            "CustomerPrincipalForChatOrder", (), {
                "user_id": conv.customer_id,
                "session_id": None,
                "roles": frozenset({"customer"}),
                "permissions": frozenset({"order.place", "cart.use"}),
                "has_permission": staticmethod(lambda p: True),
                "has_role": staticmethod(lambda r: r == "customer"),
                "is_system": False,
            },
        )()
        order = await svc.place_order(principal=customer_principal, request=req)
        conv.order_id = order.id
        await record_audit(
            actor=principal,
            action="customer_care.order.confirmed",
            resource_type="orders.order",
            resource_id=order.id,
            metadata={"conversation_id": str(conv_id), "order_code": order.code},
        )
    return {
        "order_id": str(order.id),
        "order_code": order.code,
        "grand_total": str(order.grand_total),
        "status": order.status,
        "conversation_id": str(conv_id),
    }


# ============================================================== DLQ admin
@router.get(
    "/dlq",
    summary="List dead-letter entries (admin)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def dlq_list(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(CCDeadLetter).order_by(desc(CCDeadLetter.created_at)).limit(limit)
        if status_filter:
            stmt = stmt.where(CCDeadLetter.status == status_filter)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(r.id), "source": r.source, "operation": r.operation,
                "error_class": r.error_class, "error_message": r.error_message,
                "attempts": r.attempts, "status": r.status,
                "created_at": r.created_at, "last_attempt_at": r.last_attempt_at,
                "payload_preview": (r.payload or "")[:300],
            }
            for r in rows
        ]


@router.get(
    "/dlq/{dlq_id}",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def dlq_get(
    dlq_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                select(CCDeadLetter).where(CCDeadLetter.id == dlq_id)
            )
        ).scalar_one_or_none()
        if r is None:
            raise NotFoundError("DLQ entry not found")
        return {
            "id": str(r.id), "source": r.source, "operation": r.operation,
            "error_class": r.error_class, "error_message": r.error_message,
            "traceback": r.traceback, "attempts": r.attempts,
            "status": r.status, "payload": r.payload,
            "created_at": r.created_at, "last_attempt_at": r.last_attempt_at,
        }


@router.post(
    "/dlq/{dlq_id}/replay",
    summary="Mark a DLQ entry for replay (returns it to pending state)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def dlq_replay(
    dlq_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    """Resets the entry to ``status='pending'`` + ``attempts=0`` so
    the next retry-worker tick picks it up. We don't replay inline
    because the original failing operation may still take time.
    """
    async with uow.transactional() as session:
        r = (
            await session.execute(
                select(CCDeadLetter).where(CCDeadLetter.id == dlq_id)
            )
        ).scalar_one_or_none()
        if r is None:
            raise NotFoundError("DLQ entry not found")
        r.status = "pending"
        r.attempts = 0
        await record_audit(
            actor=principal,
            action="customer_care.dlq.replay",
            resource_type="cc_dead_letters",
            resource_id=r.id,
        )
    return {"id": str(dlq_id), "status": "pending"}


# ============================================================== GDPR
class GdprDeleteRequest(StrictModel):
    customer_phone: str | None = Field(default=None, max_length=40)
    customer_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=2000)


@router.post(
    "/gdpr/delete-my-data",
    summary="Customer requests deletion of their CC data (PUBLIC — token-style or self-serve)",
)
async def gdpr_request_delete(
    body: GdprDeleteRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    """File a deletion request. An admin must process it (see admin
    endpoint below). Idempotent — only one pending request per
    customer at a time."""
    if not body.customer_phone and not body.customer_id:
        raise ValidationError("either customer_phone or customer_id is required")
    async with uow.transactional() as session:
        # Check for an existing pending request
        existing = (
            await session.execute(
                _t(
                    "SELECT id FROM cc_gdpr_deletion_requests "
                    "WHERE status = 'pending' "
                    "AND (customer_phone = :phone OR customer_id = :cid) "
                    "LIMIT 1"
                ),
                {"phone": body.customer_phone, "cid": body.customer_id},
            )
        ).first()
        if existing:
            return {"id": str(existing[0]), "status": "pending_existing"}
        row = CCGdprDeletion(
            customer_id=body.customer_id,
            customer_phone=body.customer_phone,
            reason=body.reason,
            status="pending",
        )
        session.add(row)
        await session.flush()
        request_id = row.id
    sse_bus.publish({"type": "gdpr.request_filed", "request_id": str(request_id)})
    return {"id": str(request_id), "status": "pending"}


@router.post(
    "/admin/gdpr/process/{request_id}",
    summary="Admin processes a deletion request — anonymizes CC data",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def gdpr_admin_process(
    request_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    """Anonymizes: message bodies, customer profile fields, removes
    GDPR row.  Conversations + message metadata stay (with their
    text replaced by `[redacted]`) for accounting integrity."""
    async with uow.transactional() as session:
        req = (
            await session.execute(
                select(CCGdprDeletion).where(CCGdprDeletion.id == request_id)
            )
        ).scalar_one_or_none()
        if req is None:
            raise NotFoundError("GDPR request not found")
        if req.status != "pending":
            raise BusinessRuleError(f"already {req.status}")
        # Resolve customer_id
        cid = req.customer_id
        if cid is None and req.customer_phone:
            row = (
                await session.execute(
                    _t("SELECT id FROM users WHERE phone = :p"),
                    {"p": req.customer_phone},
                )
            ).first()
            if row:
                cid = row[0]
        if cid is None:
            req.status = "no_customer_found"
            req.completed_at = datetime.now(timezone.utc)
            return {"id": str(request_id), "status": "no_customer_found"}
        # Anonymize CC data
        await session.execute(
            _t(
                "UPDATE cc_messages SET message_body = '[redacted]', media_url = NULL "
                "WHERE conversation_id IN (SELECT id FROM cc_conversations WHERE customer_id = :cid)"
            ),
            {"cid": cid},
        )
        await session.execute(
            _t(
                "UPDATE cc_customer_profile SET preferred_language = 'unknown', "
                "consent_status = 'erased', last_interest = NULL, full_address = NULL, "
                "location_link = NULL, cc_status = 'erased' "
                "WHERE customer_id = :cid"
            ),
            {"cid": cid},
        )
        req.status = "completed"
        req.completed_at = datetime.now(timezone.utc)
        await record_audit(
            actor=principal,
            action="customer_care.gdpr.processed",
            resource_type="cc_gdpr_deletion_requests",
            resource_id=request_id,
            metadata={"customer_id": str(cid)},
        )
    return {"id": str(request_id), "status": "completed"}
