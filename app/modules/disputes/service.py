"""Disputes service — orchestration layer.

Wraps repository calls with state-transition checks and triggers
side-effects (escrow holds + buyer wallet credit on refund).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.core.events.outbox import enqueue_outbox
from app.core.logging import get_logger
from app.modules.disputes import repository as repo
from app.modules.disputes.codes import (
    ESCROW_STATUS_CANCELLED,
    ESCROW_STATUS_RELEASED_TO_BUYER,
    ESCROW_STATUS_RELEASED_TO_SELLER,
    ESCROW_STATUS_SPLIT,
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_FILES,
    RESOLUTION_CUSTOMER_WITHDREW,
    RESOLUTION_DECLINE,
    RESOLUTION_REFUND_FULL,
    RESOLUTION_REFUND_PARTIAL,
    RESOLUTION_REPLACE,
    ROLE_BUYER,
    ROLE_MEDIATOR,
    ROLE_SELLER,
    ROLE_SYSTEM,
    SELLER_RESPONSE_SLA_HOURS,
    STATUS_AWAITING_BUYER,
    STATUS_AWAITING_SELLER,
    STATUS_CLOSED,
    STATUS_RESOLVED,
    STATUS_UNDER_REVIEW,
)
from app.modules.wallet.service import WalletService

_log = get_logger("hypershop.disputes.service")


class DisputeNotFound(Exception):
    pass


class DisputeNotOwned(Exception):
    pass


class DisputeAlreadyResolved(Exception):
    pass


class InvalidDisputeTransition(Exception):
    pass


class EscrowNotFound(Exception):
    pass


class EvidenceLimitExceeded(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _seller_owner_user_id(
    session: AsyncSession, seller_id: UUID,
) -> UUID | None:
    """Resolve the seller's owner user_id (the IAM user we push to).

    Falls back to NULL if seller has no `owner` role row (e.g. solo
    Hypershop Direct). The push handler skips on None.
    """
    from app.modules.sellers.models import SellerUser

    stmt = (
        select(SellerUser.user_id)
        .where(SellerUser.seller_id == seller_id, SellerUser.role == "owner")
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _emit_dispute_event(
    session: AsyncSession, *, event_type: str, dispute: Any,
) -> None:
    """Enqueue an outbox event with the standard dispute payload.

    The payload includes both `customer_user_id` + `seller_user_id`
    (the seller's owner user, not the sellers.id row) so push handlers
    can pick the right recipient by role without re-querying.
    """
    seller_user_id = await _seller_owner_user_id(session, dispute.seller_id)
    await enqueue_outbox(
        type=event_type,
        payload={
            "dispute_id": str(dispute.id),
            "order_id": str(dispute.order_id),
            "seller_id": str(dispute.seller_id),
            "customer_user_id": str(dispute.opened_by_user_id),
            "seller_user_id": str(seller_user_id) if seller_user_id else None,
            "subject": dispute.subject,
            "status": dispute.status,
            "resolution": dispute.resolution,
        },
        session=session,
    )


def _dispute_to_dict(d: Any) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "order_id": str(d.order_id),
        "order_item_id": str(d.order_item_id) if d.order_item_id else None,
        "opened_by_user_id": str(d.opened_by_user_id),
        "seller_id": str(d.seller_id),
        "dispute_type": d.dispute_type,
        "status": d.status,
        "resolution": d.resolution,
        "amount_disputed_minor": int(d.amount_disputed_minor),
        "amount_refunded_minor": int(d.amount_refunded_minor),
        "subject": d.subject,
        "description": d.description,
        "mediator_user_id": (
            str(d.mediator_user_id) if d.mediator_user_id else None
        ),
        "decision_notes": d.decision_notes,
        "opened_at": d.opened_at.isoformat() if d.opened_at else None,
        "last_response_at": (
            d.last_response_at.isoformat() if d.last_response_at else None
        ),
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
        "closed_at": d.closed_at.isoformat() if d.closed_at else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


# ─── Open / respond ──────────────────────────────────────────────


async def open_dispute(
    session: AsyncSession,
    *,
    buyer_user_id: UUID,
    order_id: UUID,
    order_item_id: UUID | None,
    dispute_type: str,
    subject: str,
    description: str | None,
    amount_disputed_minor: int,
    seller_id: UUID,
) -> dict[str, Any]:
    """Create dispute + escrow hold + initial system message."""
    existing = await repo.find_live_dispute_for_target(
        session, order_id=order_id, order_item_id=order_item_id,
    )
    if existing is not None:
        raise InvalidDisputeTransition(
            "A live dispute already exists for this order/item.",
        )

    now = _now()
    dispute = await repo.create_dispute(
        session,
        order_id=order_id,
        order_item_id=order_item_id,
        opened_by_user_id=buyer_user_id,
        seller_id=seller_id,
        dispute_type=dispute_type,
        status=STATUS_AWAITING_SELLER,
        amount_disputed_minor=amount_disputed_minor,
        subject=subject,
        description=description,
        opened_at=now,
        last_response_at=now,
    )

    await repo.create_escrow_hold(
        session,
        dispute_id=dispute.id,
        seller_id=seller_id,
        order_id=order_id,
        held_amount_minor=amount_disputed_minor,
    )

    await repo.add_message(
        session,
        dispute_id=dispute.id,
        author_user_id=None,
        author_role=ROLE_SYSTEM,
        body=(
            f"Dispute opened by buyer. Seller has "
            f"{SELLER_RESPONSE_SLA_HOURS}h to respond before auto-escalation."
        ),
        is_system=True,
    )
    _log.info("dispute_opened dispute_id=%s seller_id=%s", dispute.id, seller_id)
    await _emit_dispute_event(
        session, event_type="disputes.dispute.opened", dispute=dispute,
    )
    return _dispute_to_dict(dispute)


async def seller_respond(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    seller_id: UUID,
    body: str,
    attachments: list[Any] | None = None,
) -> dict[str, Any]:
    d = await repo.lock_dispute_for_update(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if d.seller_id != seller_id:
        raise DisputeNotOwned("Dispute is not owned by this seller.")
    if d.status in (STATUS_RESOLVED, STATUS_CLOSED):
        raise DisputeAlreadyResolved("Dispute is no longer open for responses.")

    await repo.add_message(
        session,
        dispute_id=dispute_id,
        author_user_id=seller_id,
        author_role=ROLE_SELLER,
        body=body,
        attachments=attachments,
    )
    now = _now()
    d = await repo.update_dispute(
        session, dispute_id,
        status=STATUS_AWAITING_BUYER,
        last_response_at=now,
    )
    await _emit_dispute_event(
        session, event_type="disputes.dispute.seller_responded", dispute=d,
    )
    return _dispute_to_dict(d)


async def buyer_respond(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    buyer_user_id: UUID,
    body: str,
    attachments: list[Any] | None = None,
) -> dict[str, Any]:
    d = await repo.lock_dispute_for_update(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if d.opened_by_user_id != buyer_user_id:
        raise DisputeNotOwned("Dispute is not owned by this buyer.")
    if d.status in (STATUS_RESOLVED, STATUS_CLOSED):
        raise DisputeAlreadyResolved("Dispute is no longer open for responses.")

    await repo.add_message(
        session,
        dispute_id=dispute_id,
        author_user_id=buyer_user_id,
        author_role=ROLE_BUYER,
        body=body,
        attachments=attachments,
    )
    now = _now()
    d = await repo.update_dispute(
        session, dispute_id,
        status=STATUS_AWAITING_SELLER,
        last_response_at=now,
    )
    await _emit_dispute_event(
        session, event_type="disputes.dispute.buyer_responded", dispute=d,
    )
    return _dispute_to_dict(d)


async def buyer_withdraw(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    buyer_user_id: UUID,
    reason: str | None = None,
) -> dict[str, Any]:
    d = await repo.lock_dispute_for_update(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if d.opened_by_user_id != buyer_user_id:
        raise DisputeNotOwned("Dispute is not owned by this buyer.")
    if d.status in (STATUS_RESOLVED, STATUS_CLOSED):
        raise DisputeAlreadyResolved("Dispute is already closed.")

    hold = await repo.get_escrow_for_dispute(session, dispute_id)
    if hold is None:
        raise EscrowNotFound("Escrow hold missing for dispute.")

    await repo.update_escrow_release(
        session, hold.id,
        to_buyer_minor=0,
        to_seller_minor=int(hold.held_amount_minor),
        new_status=ESCROW_STATUS_RELEASED_TO_SELLER,
        note="Buyer withdrew dispute.",
    )

    now = _now()
    d = await repo.update_dispute(
        session, dispute_id,
        status=STATUS_CLOSED,
        resolution=RESOLUTION_CUSTOMER_WITHDREW,
        resolved_at=now,
        closed_at=now,
        decision_notes=reason,
    )
    await repo.add_message(
        session,
        dispute_id=dispute_id,
        author_user_id=buyer_user_id,
        author_role=ROLE_SYSTEM,
        body=f"Buyer withdrew the dispute. Reason: {reason or 'not provided'}.",
        is_system=True,
    )
    return _dispute_to_dict(d)


async def seller_accept(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    seller_id: UUID,
) -> dict[str, Any]:
    """Seller agrees to full refund. Same effect as mediator decide(refund_full)."""
    d = await repo.lock_dispute_for_update(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if d.seller_id != seller_id:
        raise DisputeNotOwned("Dispute is not owned by this seller.")
    if d.status in (STATUS_RESOLVED, STATUS_CLOSED):
        raise DisputeAlreadyResolved("Dispute is no longer open.")

    await repo.add_message(
        session,
        dispute_id=dispute_id,
        author_user_id=seller_id,
        author_role=ROLE_SELLER,
        body="Seller accepted full refund.",
    )
    return await _settle_resolution(
        session,
        dispute=d,
        resolution=RESOLUTION_REFUND_FULL,
        refund_amount_minor=int(d.amount_disputed_minor),
        decision_notes="Seller voluntarily accepted full refund.",
        actor_user_id=seller_id,
        actor_role=ROLE_SELLER,
    )


async def seller_counter(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    seller_id: UUID,
    counter_amount_minor: int,
    message: str,
) -> dict[str, Any]:
    d = await repo.lock_dispute_for_update(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if d.seller_id != seller_id:
        raise DisputeNotOwned("Dispute is not owned by this seller.")
    if d.status in (STATUS_RESOLVED, STATUS_CLOSED):
        raise DisputeAlreadyResolved("Dispute is no longer open.")
    if counter_amount_minor > int(d.amount_disputed_minor):
        raise InvalidDisputeTransition(
            "Counter offer cannot exceed disputed amount.",
        )

    body = (
        f"Seller proposes partial refund of {counter_amount_minor} (minor). "
        f"Message: {message}"
    )
    await repo.add_message(
        session,
        dispute_id=dispute_id,
        author_user_id=seller_id,
        author_role=ROLE_SELLER,
        body=body,
        attachments=[{"kind": "counter_offer", "amount_minor": counter_amount_minor}],
    )
    now = _now()
    d = await repo.update_dispute(
        session, dispute_id,
        status=STATUS_AWAITING_BUYER,
        last_response_at=now,
    )
    return _dispute_to_dict(d)


# ─── Mediator ────────────────────────────────────────────────────


async def assign_mediator(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    mediator_user_id: UUID,
    internal_note: str | None = None,
) -> dict[str, Any]:
    d = await repo.lock_dispute_for_update(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if d.status in (STATUS_RESOLVED, STATUS_CLOSED):
        raise DisputeAlreadyResolved("Dispute is no longer open.")

    now = _now()
    d = await repo.update_dispute(
        session, dispute_id,
        status=STATUS_UNDER_REVIEW,
        mediator_user_id=mediator_user_id,
        last_response_at=now,
    )
    await repo.add_message(
        session,
        dispute_id=dispute_id,
        author_user_id=mediator_user_id,
        author_role=ROLE_SYSTEM,
        body=(
            "Mediator assigned. Dispute is now under formal review. "
            f"Note: {internal_note or 'none'}"
        ),
        is_system=True,
    )
    return _dispute_to_dict(d)


async def mediator_decide(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    mediator_user_id: UUID,
    resolution: str,
    refund_amount_minor: int,
    decision_notes: str,
) -> dict[str, Any]:
    d = await repo.lock_dispute_for_update(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if d.status in (STATUS_RESOLVED, STATUS_CLOSED):
        raise DisputeAlreadyResolved("Dispute is already resolved.")
    if d.status != STATUS_UNDER_REVIEW:
        # Escalate then decide (single step).
        d = await repo.update_dispute(
            session, dispute_id,
            status=STATUS_UNDER_REVIEW,
            mediator_user_id=mediator_user_id,
        )

    if refund_amount_minor > int(d.amount_disputed_minor):
        raise InvalidDisputeTransition(
            "Refund cannot exceed disputed amount.",
        )

    return await _settle_resolution(
        session,
        dispute=d,
        resolution=resolution,
        refund_amount_minor=refund_amount_minor,
        decision_notes=decision_notes,
        actor_user_id=mediator_user_id,
        actor_role=ROLE_MEDIATOR,
    )


async def _settle_resolution(
    session: AsyncSession,
    *,
    dispute: Any,
    resolution: str,
    refund_amount_minor: int,
    decision_notes: str,
    actor_user_id: UUID,
    actor_role: str,
) -> dict[str, Any]:
    hold = await repo.get_escrow_for_dispute(session, dispute.id)
    if hold is None:
        raise EscrowNotFound("Escrow hold missing for dispute.")

    held = int(hold.held_amount_minor)
    to_buyer = 0
    to_seller = 0
    new_escrow_status = hold.status
    actual_refund = 0

    if resolution == RESOLUTION_REFUND_FULL:
        to_buyer = held
        to_seller = 0
        new_escrow_status = ESCROW_STATUS_RELEASED_TO_BUYER
        actual_refund = held
    elif resolution == RESOLUTION_REFUND_PARTIAL:
        if refund_amount_minor <= 0:
            raise InvalidDisputeTransition(
                "Partial refund must be > 0.",
            )
        if refund_amount_minor >= held:
            to_buyer = held
            to_seller = 0
            new_escrow_status = ESCROW_STATUS_RELEASED_TO_BUYER
        else:
            to_buyer = refund_amount_minor
            to_seller = held - refund_amount_minor
            new_escrow_status = ESCROW_STATUS_SPLIT
        actual_refund = to_buyer
    elif resolution == RESOLUTION_REPLACE:
        # Escrow stays active until replacement is delivered;
        # close_resolved_dispute or a follow-up decide will release it.
        actual_refund = 0
    elif resolution == RESOLUTION_DECLINE:
        to_buyer = 0
        to_seller = held
        new_escrow_status = ESCROW_STATUS_RELEASED_TO_SELLER
        actual_refund = 0
    else:
        raise InvalidDisputeTransition(f"Unknown resolution: {resolution}.")

    if resolution != RESOLUTION_REPLACE:
        await repo.update_escrow_release(
            session, hold.id,
            to_buyer_minor=to_buyer,
            to_seller_minor=to_seller,
            new_status=new_escrow_status,
            note=f"{resolution} — {decision_notes[:200]}",
        )

    if actual_refund > 0:
        wallet = WalletService(session)
        await wallet.credit(
            user_id=dispute.opened_by_user_id,
            amount_minor=actual_refund,
            source_type="dispute_refund",
            source_id=dispute.id,
            memo=f"Dispute {dispute.id} refund ({resolution})",
        )

    now = _now()
    d = await repo.update_dispute(
        session, dispute.id,
        status=STATUS_RESOLVED,
        resolution=resolution,
        amount_refunded_minor=int(dispute.amount_refunded_minor) + actual_refund,
        decision_notes=decision_notes,
        resolved_at=now,
        last_response_at=now,
    )

    await repo.add_message(
        session,
        dispute_id=dispute.id,
        author_user_id=actor_user_id,
        author_role=ROLE_SYSTEM,
        body=(
            f"Resolved by {actor_role}: {resolution}. "
            f"Refund credited: {actual_refund} (minor). "
            f"Notes: {decision_notes[:500]}"
        ),
        is_system=True,
    )
    _log.info(
        "dispute_resolved dispute_id=%s resolution=%s refund_minor=%d",
        dispute.id, resolution, actual_refund,
    )
    await _emit_dispute_event(
        session, event_type="disputes.dispute.resolved", dispute=d,
    )
    return _dispute_to_dict(d)


async def close_resolved_dispute(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    actor_user_id: UUID,
) -> dict[str, Any]:
    d = await repo.lock_dispute_for_update(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if d.status != STATUS_RESOLVED:
        raise InvalidDisputeTransition(
            "Only resolved disputes can be closed.",
        )

    # If a replace resolution left the hold active, cancel it now (the
    # replacement was delivered, no further refund expected).
    hold = await repo.get_escrow_for_dispute(session, dispute_id)
    if hold is not None and hold.status == "active":
        await repo.update_escrow_release(
            session, hold.id,
            to_buyer_minor=int(hold.released_to_buyer_minor),
            to_seller_minor=int(hold.released_to_seller_minor),
            new_status=ESCROW_STATUS_CANCELLED,
            note="Dispute closed after replace.",
        )

    now = _now()
    d = await repo.update_dispute(
        session, dispute_id,
        status=STATUS_CLOSED,
        closed_at=now,
    )
    await repo.add_message(
        session,
        dispute_id=dispute_id,
        author_user_id=actor_user_id,
        author_role=ROLE_SYSTEM,
        body="Dispute closed.",
        is_system=True,
    )
    await _emit_dispute_event(
        session, event_type="disputes.dispute.closed", dispute=d,
    )
    return _dispute_to_dict(d)


# ─── Evidence ────────────────────────────────────────────────────


async def add_evidence(
    session: AsyncSession,
    *,
    dispute_id: UUID,
    uploaded_by_user_id: UUID,
    uploader_role: str,
    file_url: str,
    content_type: str,
    size_bytes: int,
    description: str | None = None,
) -> dict[str, Any]:
    d = await repo.get_dispute(session, dispute_id)
    if d is None:
        raise DisputeNotFound("Dispute not found.")
    if size_bytes > MAX_EVIDENCE_BYTES:
        raise EvidenceLimitExceeded(
            f"File exceeds max size {MAX_EVIDENCE_BYTES} bytes.",
        )
    existing = await repo.count_evidence_for_dispute(session, dispute_id)
    if existing >= MAX_EVIDENCE_FILES:
        raise EvidenceLimitExceeded(
            f"Max {MAX_EVIDENCE_FILES} evidence files per dispute.",
        )
    row = await repo.add_evidence(
        session,
        dispute_id=dispute_id,
        uploaded_by_user_id=uploaded_by_user_id,
        uploader_role=uploader_role,
        file_url=file_url,
        content_type=content_type,
        size_bytes=size_bytes,
        description=description,
    )
    return {
        "id": str(row.id),
        "dispute_id": str(row.dispute_id),
        "uploaded_by_user_id": str(row.uploaded_by_user_id),
        "uploader_role": row.uploader_role,
        "file_url": row.file_url,
        "content_type": row.content_type,
        "size_bytes": int(row.size_bytes),
        "description": row.description,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ─── ARQ job hook ────────────────────────────────────────────────


async def auto_escalate_overdue(
    session: AsyncSession,
    *,
    sla_hours: int = SELLER_RESPONSE_SLA_HOURS,
) -> dict[str, int]:
    rows = await repo.find_overdue_seller_responses(session, sla_hours)
    escalated = 0
    for d in rows:
        d = await repo.update_dispute(
            session, d.id,
            status=STATUS_UNDER_REVIEW,
            last_response_at=_now(),
        )
        await repo.add_message(
            session,
            dispute_id=d.id,
            author_user_id=None,
            author_role=ROLE_SYSTEM,
            body=(
                f"Auto-escalated to mediator review — seller did not respond "
                f"within {sla_hours}h."
            ),
            is_system=True,
        )
        await _emit_dispute_event(
            session, event_type="disputes.dispute.escalated", dispute=d,
        )
        escalated += 1
    return {"escalated": escalated, "scanned": len(rows)}
