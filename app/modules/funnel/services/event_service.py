"""Track a funnel event.

Adapted from the sync source zip to async SQLAlchemy 2.0:

* ``session.query(...)`` → ``await session.execute(select(...))``
* ``db.commit()`` is the responsibility of the dependency (we don't
  manage the transaction inside the service so it composes cleanly
  with other writes).
* Idempotency conflict path uses a fresh ``SELECT`` after rollback
  rather than recursive call to avoid loops if the second insert also
  loses a race.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.funnel.models import (
    FunnelCustomer,
    FunnelEvent,
    FunnelFollowUpTask,
)
from app.modules.funnel.schemas import TrackEventRequest
from app.modules.funnel.services.privacy import can_contact, sha256_or_none
from app.modules.funnel.services.scoring import (
    CORE_EVENTS,
    MAX_TOTAL_SCORE,
    calculate_segment,
    get_event_score,
    recommended_action,
)

FOLLOWUP_TEMPLATE_BY_SEGMENT: dict[str, str] = {
    "Hot Lead": "hot_lead_product_help",
    "Cart Abandoner": "cart_recovery",
    "Checkout Dropper": "checkout_help",
    "Payment Failed Hot Lead": "payment_retry",
}


async def _get_customer_by_external_id(
    db: AsyncSession, external_id: str,
) -> FunnelCustomer | None:
    res = await db.execute(
        select(FunnelCustomer).where(
            FunnelCustomer.external_customer_id == external_id,
        ),
    )
    return res.scalar_one_or_none()


async def _get_event_by_idem(
    db: AsyncSession, idem: str,
) -> FunnelEvent | None:
    res = await db.execute(
        select(FunnelEvent).where(FunnelEvent.idempotency_key == idem),
    )
    return res.scalar_one_or_none()


async def _get_customer_by_id(
    db: AsyncSession, cid: int,
) -> FunnelCustomer | None:
    res = await db.execute(select(FunnelCustomer).where(FunnelCustomer.id == cid))
    return res.scalar_one_or_none()


async def get_or_create_customer(
    db: AsyncSession, payload: TrackEventRequest,
) -> FunnelCustomer:
    customer = await _get_customer_by_external_id(db, payload.external_customer_id)

    if not customer:
        customer = FunnelCustomer(
            external_customer_id=payload.external_customer_id,
            hypershop_customer_id=payload.hypershop_customer_id,
            name=payload.name,
            phone=payload.phone,
            email=payload.email,
        )
        db.add(customer)
        await db.flush()

    if payload.hypershop_customer_id:
        customer.hypershop_customer_id = payload.hypershop_customer_id
    if payload.name:
        customer.name = payload.name
    if payload.phone:
        customer.phone = payload.phone
    if payload.email:
        customer.email = payload.email

    if payload.marketing_consent is not None:
        customer.marketing_consent = payload.marketing_consent
    if payload.whatsapp_consent is not None:
        customer.whatsapp_consent = payload.whatsapp_consent
    if payload.sms_consent is not None:
        customer.sms_consent = payload.sms_consent
    if payload.ad_retargeting_consent is not None:
        customer.ad_retargeting_consent = payload.ad_retargeting_consent

    return customer


async def create_followup_if_allowed(
    db: AsyncSession, customer: FunnelCustomer, segment: str,
) -> FunnelFollowUpTask | None:
    template = FOLLOWUP_TEMPLATE_BY_SEGMENT.get(segment)
    if not template:
        return None

    allowed, blocked_reason = can_contact(customer, "whatsapp")
    task = FunnelFollowUpTask(
        customer_id=customer.id,
        channel="whatsapp",
        reason=segment,
        message_template_key=template,
        status="pending" if allowed else "blocked",
        blocked_reason=blocked_reason,
    )
    db.add(task)
    return task


async def track_event(
    db: AsyncSession,
    payload: TrackEventRequest,
    user_agent: str | None,
    ip_address: str | None,
) -> dict:
    # Phase-1 allowlist gate. Reject anything not in CORE_EVENTS so junk
    # signals never enter the funnel and pollute the KPI denominators.
    # 422 (validation) would be technically correct, but 400 with a
    # machine-readable error code makes it easy for emitters to detect
    # "this event is not yet supported" and stop spamming the endpoint.
    if payload.event_name not in CORE_EVENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "event_not_in_allowlist",
                "event_name": payload.event_name,
                "allowed_events": sorted(CORE_EVENTS),
                "message": (
                    "Funnel Phase 1 only accepts the 7 core events. "
                    "Verify the upstream emitter and confirm with the "
                    "funnel owner before adding more events to "
                    "scoring.CORE_EVENTS."
                ),
            },
        )

    existing = await _get_event_by_idem(db, payload.idempotency_key)
    if existing:
        customer = await _get_customer_by_id(db, existing.customer_id)
        return {
            "accepted": True,
            "duplicate": True,
            "customer_id": customer.id,
            "external_customer_id": customer.external_customer_id,
            "event_name": existing.event_name,
            "added_score": 0,
            "total_score": customer.current_score,
            "segment": customer.segment,
            "recommended_action": recommended_action(customer.segment),
            "privacy_notice": "Duplicate event ignored by idempotency key.",
        }

    customer = await get_or_create_customer(db, payload)
    added_score = get_event_score(payload.event_name)

    event = FunnelEvent(
        customer_id=customer.id,
        idempotency_key=payload.idempotency_key,
        source=payload.source,
        event_name=payload.event_name,
        product_id=payload.product_id,
        category_id=payload.category_id,
        campaign_id=payload.campaign_id,
        session_id=payload.session_id,
        value=payload.value,
        score_delta=added_score,
        metadata_json=json.dumps(payload.metadata or {}),
        user_agent_hash=sha256_or_none(user_agent),
        ip_hash=sha256_or_none(ip_address),
    )
    db.add(event)

    customer.current_score = min(customer.current_score + added_score, MAX_TOTAL_SCORE)
    customer.segment = calculate_segment(customer.current_score, payload.event_name)
    customer.last_event_name = payload.event_name
    customer.last_activity_at = datetime.now(timezone.utc)

    await create_followup_if_allowed(db, customer, customer.segment)

    try:
        await db.commit()
    except IntegrityError:
        # Concurrent insert with same idempotency_key — rollback and
        # return the existing record (don't recurse).
        await db.rollback()
        existing = await _get_event_by_idem(db, payload.idempotency_key)
        if existing:
            customer = await _get_customer_by_id(db, existing.customer_id)
            return {
                "accepted": True,
                "duplicate": True,
                "customer_id": customer.id,
                "external_customer_id": customer.external_customer_id,
                "event_name": existing.event_name,
                "added_score": 0,
                "total_score": customer.current_score,
                "segment": customer.segment,
                "recommended_action": recommended_action(customer.segment),
                "privacy_notice": "Duplicate event ignored by idempotency key (race).",
            }
        raise

    await db.refresh(customer)

    return {
        "accepted": True,
        "duplicate": False,
        "customer_id": customer.id,
        "external_customer_id": customer.external_customer_id,
        "event_name": payload.event_name,
        "added_score": added_score,
        "total_score": customer.current_score,
        "segment": customer.segment,
        "recommended_action": recommended_action(customer.segment),
        "privacy_notice": "PII is stored minimally. Export/contact requires customer consent.",
    }
