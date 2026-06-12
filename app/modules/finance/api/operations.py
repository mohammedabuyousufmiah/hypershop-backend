"""Finance Manager operational admin routes.

Mounted under ``/api/v1/admin/finance-ops/`` (kept distinct from the
existing ``/admin/finance/`` accounting routes in ``admin.py``). Every
mutator endpoint funnels through ``execute_finance_action`` so the
audit log + matrix gate fire in the same transaction.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.modules.finance.operations_actions import FinanceAction
from app.modules.finance.operations_models import (
    FinanceAuditLog,
    FinanceCodSettlement,
    FinanceRefundApproval,
    FinanceRiderPayoutBatch,
    FinanceSellerPayoutBatch,
)
from app.modules.finance.operations_service import (
    FinanceActionPayload,
    FinanceActor,
    FinanceRuleViolation,
    apply_cod_settlement_decision,
    apply_refund_decision,
    apply_rider_payout_decision,
    apply_seller_payout_decision,
    execute_finance_action,
)


router = APIRouter(
    prefix="/admin/finance-ops",
    tags=["finance-operations"],
)


# ============================================================
#  Schemas
# ============================================================
class _ActionBody(BaseModel):
    """Common request body for every state-changing endpoint.

    The actor is derived from the authenticated principal; the rest
    comes from the operator's modal form.
    """

    reason: str = Field(..., min_length=4, max_length=2000)
    evidence_url: str | None = Field(default=None, max_length=512)
    reference_id: str | None = Field(default=None, max_length=80)
    amount_minor: int | None = Field(default=None, ge=1)
    currency: str = Field(default="BDT", min_length=3, max_length=8)
    requested_by: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] | None = None


class _RefundOut(BaseModel):
    id: UUID
    order_id: str
    amount_minor: int
    currency: str
    status: str
    reason: str
    requested_by: str
    decided_by: str | None
    decision_audit_log_id: UUID | None

    model_config = {"from_attributes": True}


class _AuditLogOut(BaseModel):
    id: UUID
    audit_code: str
    actor_id: str
    actor_role: str
    action: str
    entity_type: str
    entity_id: str
    new_status: str
    reason: str
    amount_minor: int | None
    currency: str
    reference_id: str | None

    model_config = {"from_attributes": True}


# ============================================================
#  Helpers
# ============================================================
def _actor_from_principal(principal: Principal) -> FinanceActor:
    """Derive a FinanceActor from the request principal.

    Uses the primary role string. When a user has multiple roles we
    take the most authoritative one for the matrix check —
    ``super_admin > admin > finance_manager > <other>``.
    """
    roles = list(getattr(principal, "roles", []) or [])
    for preferred in ("super_admin", "admin", "finance_manager"):
        if preferred in roles:
            return FinanceActor(actor_id=str(principal.user_id), role=preferred)
    fallback = roles[0] if roles else "guest"
    return FinanceActor(actor_id=str(principal.user_id), role=fallback)


def _payload_for(
    request: Request,
    body: _ActionBody,
    *,
    entity_type: str,
    entity_id: str,
    new_status: str,
    order_id: str | None = None,
) -> FinanceActionPayload:
    return FinanceActionPayload(
        entity_type=entity_type,
        entity_id=entity_id,
        new_status=new_status,
        reason=body.reason,
        order_id=order_id,
        evidence_url=body.evidence_url,
        amount_minor=body.amount_minor,
        currency=body.currency,
        reference_id=body.reference_id,
        requested_by=body.requested_by,
        ip_address=request.client.host if request.client else None,
        device_info=request.headers.get("User-Agent"),
        metadata=body.metadata,
    )


def _raise_rule(e: FinanceRuleViolation) -> None:
    raise HTTPException(
        status_code=e.status_code,
        detail={"code": e.code, "message": e.message},
    )


# ============================================================
#  Refunds
# ============================================================
@router.get("/refunds", response_model=list[_RefundOut])
async def list_refunds(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[FinanceRefundApproval]:
    async with uow.transactional() as session:
        stmt = select(FinanceRefundApproval).order_by(
            FinanceRefundApproval.requested_at.desc(),
        ).limit(limit)
        if status:
            stmt = stmt.where(FinanceRefundApproval.status == status)
        rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@router.post("/refunds/{refund_id}/approve", response_model=_RefundOut)
async def approve_refund(
    request: Request,
    refund_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> FinanceRefundApproval:
    actor = _actor_from_principal(principal)
    async with uow.transactional() as session:
        refund = await session.get(FinanceRefundApproval, refund_id)
        if refund is None:
            raise HTTPException(status_code=404, detail="Refund not found.")
        # Ensure the body amount matches the refund row (Finance
        # cannot silently change the amount during approval).
        if body.amount_minor and body.amount_minor != refund.amount_minor:
            raise HTTPException(
                status_code=422,
                detail={"code": "amount_mismatch",
                        "message": "Approval amount must equal refund amount."},
            )
        body.amount_minor = refund.amount_minor
        body.requested_by = refund.requested_by
        try:
            audit = await execute_finance_action(
                session=session,
                actor=actor,
                action=FinanceAction.APPROVE_REFUND,
                payload=_payload_for(
                    request, body,
                    entity_type="refund_approval",
                    entity_id=str(refund_id),
                    new_status="approved",
                    order_id=refund.order_id,
                ),
            )
            row = await apply_refund_decision(
                session,
                refund_id=refund_id,
                decided_by=actor.actor_id,
                audit_log_id=audit.id,
                new_status="approved",
                note=body.note,
            )
        except FinanceRuleViolation as e:
            _raise_rule(e)
    return row


@router.post("/refunds/{refund_id}/reject", response_model=_RefundOut)
async def reject_refund(
    request: Request,
    refund_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> FinanceRefundApproval:
    actor = _actor_from_principal(principal)
    async with uow.transactional() as session:
        refund = await session.get(FinanceRefundApproval, refund_id)
        if refund is None:
            raise HTTPException(status_code=404, detail="Refund not found.")
        body.requested_by = refund.requested_by
        try:
            audit = await execute_finance_action(
                session=session,
                actor=actor,
                action=FinanceAction.REJECT_REFUND,
                payload=_payload_for(
                    request, body,
                    entity_type="refund_approval",
                    entity_id=str(refund_id),
                    new_status="rejected",
                    order_id=refund.order_id,
                ),
            )
            row = await apply_refund_decision(
                session,
                refund_id=refund_id,
                decided_by=actor.actor_id,
                audit_log_id=audit.id,
                new_status="rejected",
                note=body.note,
            )
        except FinanceRuleViolation as e:
            _raise_rule(e)
    return row


# ============================================================
#  COD settlements
# ============================================================
@router.get("/cod-settlements")
async def list_cod_settlements(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(FinanceCodSettlement).order_by(
            FinanceCodSettlement.handover_date.desc(),
        ).limit(limit)
        if status:
            stmt = stmt.where(FinanceCodSettlement.status == status)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "settlement_code": r.settlement_code,
            "rider_id": r.rider_id,
            "handover_date": r.handover_date.isoformat(),
            "expected_minor": r.expected_cod_minor,
            "collected_minor": r.collected_cod_minor,
            "deposited_minor": r.deposited_cod_minor,
            "mismatch_minor": r.mismatch_minor,
            "status": r.status,
        }
        for r in rows
    ]


@router.post("/cod-settlements/{settlement_id}/verify")
async def verify_cod_settlement(
    request: Request,
    settlement_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    actor = _actor_from_principal(principal)
    async with uow.transactional() as session:
        s = await session.get(FinanceCodSettlement, settlement_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Settlement not found.")
        body.amount_minor = body.amount_minor or s.collected_cod_minor
        try:
            audit = await execute_finance_action(
                session=session,
                actor=actor,
                action=FinanceAction.VERIFY_RIDER_COD_SETTLEMENT,
                payload=_payload_for(
                    request, body,
                    entity_type="cod_settlement",
                    entity_id=str(settlement_id),
                    new_status="verified",
                ),
            )
            row = await apply_cod_settlement_decision(
                session,
                settlement_id=settlement_id,
                verified_by=actor.actor_id,
                audit_log_id=audit.id,
                new_status="verified",
                note=body.note,
            )
        except FinanceRuleViolation as e:
            _raise_rule(e)
    return {"id": str(row.id), "status": row.status, "audit_code": audit.audit_code}


# ============================================================
#  Seller payouts
# ============================================================
@router.get("/seller-payouts")
async def list_seller_payouts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(FinanceSellerPayoutBatch).order_by(
            FinanceSellerPayoutBatch.period_end.desc(),
        ).limit(limit)
        if status:
            stmt = stmt.where(FinanceSellerPayoutBatch.status == status)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "batch_code": r.batch_code,
            "seller_id": r.seller_id,
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(),
            "net_minor": r.net_minor,
            "status": r.status,
        }
        for r in rows
    ]


@router.post("/seller-payouts/{batch_id}/approve")
async def approve_seller_payout(
    request: Request,
    batch_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    actor = _actor_from_principal(principal)
    async with uow.transactional() as session:
        b = await session.get(FinanceSellerPayoutBatch, batch_id)
        if b is None:
            raise HTTPException(status_code=404, detail="Batch not found.")
        body.amount_minor = body.amount_minor or b.net_minor
        try:
            audit = await execute_finance_action(
                session=session,
                actor=actor,
                action=FinanceAction.APPROVE_SELLER_PAYOUT_BATCH,
                payload=_payload_for(
                    request, body,
                    entity_type="seller_payout_batch",
                    entity_id=str(batch_id),
                    new_status="approved",
                ),
            )
            row = await apply_seller_payout_decision(
                session,
                batch_id=batch_id,
                actor_id=actor.actor_id,
                audit_log_id=audit.id,
                new_status="approved",
            )
        except FinanceRuleViolation as e:
            _raise_rule(e)
    return {"id": str(row.id), "status": row.status, "audit_code": audit.audit_code}


@router.post("/seller-payouts/{batch_id}/hold")
async def hold_seller_payout(
    request: Request,
    batch_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    actor = _actor_from_principal(principal)
    async with uow.transactional() as session:
        b = await session.get(FinanceSellerPayoutBatch, batch_id)
        if b is None:
            raise HTTPException(status_code=404, detail="Batch not found.")
        body.amount_minor = body.amount_minor or b.net_minor
        try:
            audit = await execute_finance_action(
                session=session,
                actor=actor,
                action=FinanceAction.HOLD_SELLER_PAYOUT,
                payload=_payload_for(
                    request, body,
                    entity_type="seller_payout_batch",
                    entity_id=str(batch_id),
                    new_status="held",
                ),
            )
            row = await apply_seller_payout_decision(
                session,
                batch_id=batch_id,
                actor_id=actor.actor_id,
                audit_log_id=audit.id,
                new_status="held",
                hold_reason=body.reason,
            )
        except FinanceRuleViolation as e:
            _raise_rule(e)
    return {"id": str(row.id), "status": row.status, "audit_code": audit.audit_code}


# ============================================================
#  Rider payouts (symmetric to seller payouts)
# ============================================================
@router.get("/rider-payouts")
async def list_rider_payouts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(FinanceRiderPayoutBatch).order_by(
            FinanceRiderPayoutBatch.period_end.desc(),
        ).limit(limit)
        if status:
            stmt = stmt.where(FinanceRiderPayoutBatch.status == status)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "batch_code": r.batch_code,
            "rider_id": r.rider_id,
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(),
            "net_minor": r.net_minor,
            "status": r.status,
        }
        for r in rows
    ]


@router.post("/rider-payouts/{batch_id}/approve")
async def approve_rider_payout(
    request: Request,
    batch_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    actor = _actor_from_principal(principal)
    async with uow.transactional() as session:
        b = await session.get(FinanceRiderPayoutBatch, batch_id)
        if b is None:
            raise HTTPException(status_code=404, detail="Batch not found.")
        body.amount_minor = body.amount_minor or b.net_minor
        try:
            audit = await execute_finance_action(
                session=session,
                actor=actor,
                action=FinanceAction.APPROVE_RIDER_PAYOUT_BATCH,
                payload=_payload_for(
                    request, body,
                    entity_type="rider_payout_batch",
                    entity_id=str(batch_id),
                    new_status="approved",
                ),
            )
            row = await apply_rider_payout_decision(
                session,
                batch_id=batch_id,
                actor_id=actor.actor_id,
                audit_log_id=audit.id,
                new_status="approved",
            )
        except FinanceRuleViolation as e:
            _raise_rule(e)
    return {"id": str(row.id), "status": row.status, "audit_code": audit.audit_code}


# ============================================================
#  Audit log read
# ============================================================
@router.get("/audit-logs", response_model=list[_AuditLogOut])
async def list_audit_logs(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[FinanceAuditLog]:
    async with uow.transactional() as session:
        stmt = select(FinanceAuditLog).order_by(
            FinanceAuditLog.created_at.desc(),
        ).limit(limit)
        if entity_type:
            stmt = stmt.where(FinanceAuditLog.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(FinanceAuditLog.entity_id == entity_id)
        if action:
            stmt = stmt.where(FinanceAuditLog.action == action)
        rows = (await session.execute(stmt)).scalars().all()
    return list(rows)
