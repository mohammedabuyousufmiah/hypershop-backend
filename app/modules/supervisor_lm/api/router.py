"""Supervisor + Last-Mile Manager HTTP API.

Exposes the 6 service entry points + 4 list/get endpoints under
``/admin/supervisor/*``. Wired 2026-05-29 (was dead-code; service
existed since Phase D but no router was mounted).

Endpoints:

    POST /admin/supervisor/actions             — supervisor verbs
    POST /admin/supervisor/escalations         — open order escalation
    POST /admin/supervisor/risk-flags          — flag seller/rider/customer
    POST /admin/supervisor/failed-delivery-reviews
    POST /admin/supervisor/manager-approvals   — manager creates approval
    POST /admin/supervisor/manager-approvals/{id}/decide
    GET  /admin/supervisor/manager-approvals   — list pending
    GET  /admin/supervisor/escalations         — list open
    GET  /admin/supervisor/risk-flags          — list active
    GET  /admin/supervisor/failed-delivery-reviews
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import Field
from sqlalchemy import desc, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.iam.permissions import (
    P_MANAGER_DECISION,
    P_SUPERVISOR_ACTION,
    P_SUPERVISOR_RISK_FLAG,
    P_SUPERVISOR_VIEW,
)
from app.modules.supervisor_lm.models import (
    FailedDeliveryReview,
    ManagerApproval,
    OrderEscalation,
    RiskFlag,
)
from app.modules.supervisor_lm.service import (
    ManagerApprovalType,
    SupervisorActionType,
    SupervisorActor,
    SupervisorLmRuleViolation,
    create_failed_delivery_review,
    create_manager_approval,
    create_order_escalation,
    create_risk_flag,
    record_manager_decision,
    record_supervisor_action,
)

_log = get_logger("hypershop.supervisor_lm")

router = APIRouter(
    prefix="/admin/supervisor",
    tags=["admin-supervisor-lm"],
)


def _actor(principal: Principal) -> SupervisorActor:
    return SupervisorActor(
        actor_id=str(principal.user_id),
        role=principal.primary_role or "supervisor",
    )


def _handle_violation(exc: SupervisorLmRuleViolation) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


# ============================================================
#  Request schemas
# ============================================================
class SupervisorActionIn(StrictModel):
    action_type: SupervisorActionType
    entity_type: str = Field(min_length=1, max_length=64)
    entity_id: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=2000)
    order_id: str | None = Field(default=None, max_length=80)
    evidence_url: str | None = Field(default=None, max_length=1000)
    rule_id: UUID | None = None
    requires_manager_approval: bool = False


class EscalationIn(StrictModel):
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    order_id: str | None = Field(default=None, max_length=80)
    priority: str = Field(default="normal", pattern=r"^(low|normal|high|critical)$")
    evidence_url: str | None = Field(default=None, max_length=1000)


class RiskFlagIn(StrictModel):
    actor_type: str = Field(pattern=r"^(seller|rider|customer)$")
    target_actor_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    order_id: str | None = Field(default=None, max_length=80)
    evidence_url: str | None = Field(default=None, max_length=1000)


class FailedDeliveryReviewIn(StrictModel):
    order_id: str = Field(min_length=1, max_length=80)
    delivery_task_id: str = Field(min_length=1, max_length=80)
    delivery_attempt_id: str | None = Field(default=None, max_length=80)
    rider_note: str = Field(min_length=1, max_length=2000)
    call_attempt: str = Field(min_length=1, max_length=200)
    gps_location: str = Field(min_length=1, max_length=200)
    photo_evidence_url: str | None = Field(default=None, max_length=1000)


class ManagerApprovalIn(StrictModel):
    approval_type: ManagerApprovalType
    request_reason: str = Field(min_length=1, max_length=2000)
    order_id: str | None = Field(default=None, max_length=80)
    escalation_id: UUID | None = None
    priority: str = Field(default="normal", pattern=r"^(low|normal|high|critical)$")
    evidence_url: str | None = Field(default=None, max_length=1000)
    sla_due_at: datetime | None = None


class ManagerDecisionIn(StrictModel):
    decision: str = Field(pattern=r"^(approved|rejected)$")
    decision_reason: str = Field(min_length=1, max_length=2000)
    evidence_url: str | None = Field(default=None, max_length=1000)


# ============================================================
#  Writes
# ============================================================
@router.post("/actions", status_code=201)
async def post_supervisor_action(
    body: SupervisorActionIn,
    principal: Annotated[Principal, Depends(get_current_principal)],
    _gate: Annotated[Principal, Depends(requires_permission(P_SUPERVISOR_ACTION))],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow as u:
        try:
            row = await record_supervisor_action(
                session=u.session,
                actor=_actor(principal),
                action_type=body.action_type,
                entity_type=body.entity_type,
                entity_id=body.entity_id,
                reason=body.reason,
                order_id=body.order_id,
                evidence_url=body.evidence_url,
                rule_id=body.rule_id,
                requires_manager_approval=body.requires_manager_approval,
            )
        except SupervisorLmRuleViolation as e:
            raise _handle_violation(e) from e
    return {
        "id": str(row.id),
        "action_code": row.action_code,
        "approval_status": row.approval_status,
    }


@router.post("/escalations", status_code=201)
async def post_escalation(
    body: EscalationIn,
    principal: Annotated[Principal, Depends(get_current_principal)],
    _gate: Annotated[Principal, Depends(requires_permission(P_SUPERVISOR_ACTION))],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow as u:
        try:
            row = await create_order_escalation(
                u.session,
                actor=_actor(principal),
                subject_type=body.subject_type,
                subject_id=body.subject_id,
                reason=body.reason,
                order_id=body.order_id,
                priority=body.priority,
                evidence_url=body.evidence_url,
            )
        except SupervisorLmRuleViolation as e:
            raise _handle_violation(e) from e
    return {
        "id": str(row.id),
        "escalation_code": row.escalation_code,
        "status": row.status,
    }


@router.post("/risk-flags", status_code=201)
async def post_risk_flag(
    body: RiskFlagIn,
    principal: Annotated[Principal, Depends(get_current_principal)],
    _gate: Annotated[Principal, Depends(requires_permission(P_SUPERVISOR_RISK_FLAG))],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow as u:
        try:
            row = await create_risk_flag(
                u.session,
                actor=_actor(principal),
                actor_type=body.actor_type,
                target_actor_id=body.target_actor_id,
                reason=body.reason,
                order_id=body.order_id,
                evidence_url=body.evidence_url,
            )
        except SupervisorLmRuleViolation as e:
            raise _handle_violation(e) from e
    return {"id": str(row.id), "flag_code": row.flag_code}


@router.post("/failed-delivery-reviews", status_code=201)
async def post_failed_delivery_review(
    body: FailedDeliveryReviewIn,
    principal: Annotated[Principal, Depends(get_current_principal)],
    _gate: Annotated[Principal, Depends(requires_permission(P_SUPERVISOR_ACTION))],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow as u:
        try:
            row = await create_failed_delivery_review(
                u.session,
                actor=_actor(principal),
                order_id=body.order_id,
                delivery_task_id=body.delivery_task_id,
                delivery_attempt_id=body.delivery_attempt_id,
                rider_note=body.rider_note,
                call_attempt=body.call_attempt,
                gps_location=body.gps_location,
                photo_evidence_url=body.photo_evidence_url,
            )
        except SupervisorLmRuleViolation as e:
            raise _handle_violation(e) from e
    return {"id": str(row.id), "review_code": row.review_code}


@router.post("/manager-approvals", status_code=201)
async def post_manager_approval(
    body: ManagerApprovalIn,
    principal: Annotated[Principal, Depends(get_current_principal)],
    _gate: Annotated[Principal, Depends(requires_permission(P_MANAGER_DECISION))],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow as u:
        row = await create_manager_approval(
            u.session,
            approval_type=body.approval_type,
            requested_by=str(principal.user_id),
            request_reason=body.request_reason,
            order_id=body.order_id,
            escalation_id=body.escalation_id,
            priority=body.priority,
            evidence_url=body.evidence_url,
            sla_due_at=body.sla_due_at,
        )
    return {
        "id": str(row.id),
        "approval_code": row.approval_code,
        "status": row.status,
    }


@router.post("/manager-approvals/{approval_id}/decide")
async def post_manager_decision(
    approval_id: Annotated[UUID, Path()],
    body: ManagerDecisionIn,
    principal: Annotated[Principal, Depends(get_current_principal)],
    _gate: Annotated[Principal, Depends(requires_permission(P_MANAGER_DECISION))],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow as u:
        try:
            approval, action = await record_manager_decision(
                session=u.session,
                actor=_actor(principal),
                approval_id=approval_id,
                decision=body.decision,
                decision_reason=body.decision_reason,
                evidence_url=body.evidence_url,
            )
        except SupervisorLmRuleViolation as e:
            raise _handle_violation(e) from e
    return {
        "approval_id": str(approval.id),
        "status": approval.status,
        "action_id": str(action.id),
    }


# ============================================================
#  Reads (lists)
# ============================================================
@router.get("/manager-approvals")
async def list_manager_approvals(
    status: Annotated[str | None, Query()] = None,
    priority: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    _gate: Annotated[Principal, Depends(requires_permission(P_SUPERVISOR_VIEW))] = None,
    uow: Annotated[UnitOfWork, Depends(get_uow)] = None,
) -> dict[str, Any]:
    async with uow as u:
        q = select(ManagerApproval).order_by(desc(ManagerApproval.created_at)).limit(limit)
        if status:
            q = q.where(ManagerApproval.status == status)
        if priority:
            q = q.where(ManagerApproval.priority == priority)
        rows = (await u.session.execute(q)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "approval_code": r.approval_code,
                "approval_type": r.approval_type,
                "status": r.status,
                "priority": r.priority,
                "requested_by": r.requested_by,
                "order_id": r.order_id,
                "sla_due_at": r.sla_due_at.isoformat() if r.sla_due_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/escalations")
async def list_escalations(
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    _gate: Annotated[Principal, Depends(requires_permission(P_SUPERVISOR_VIEW))] = None,
    uow: Annotated[UnitOfWork, Depends(get_uow)] = None,
) -> dict[str, Any]:
    async with uow as u:
        q = select(OrderEscalation).order_by(desc(OrderEscalation.created_at)).limit(limit)
        if status:
            q = q.where(OrderEscalation.status == status)
        rows = (await u.session.execute(q)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "escalation_code": r.escalation_code,
                "subject_type": r.subject_type,
                "subject_id": r.subject_id,
                "status": r.status,
                "priority": r.priority,
                "order_id": r.order_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/risk-flags")
async def list_risk_flags(
    actor_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    _gate: Annotated[Principal, Depends(requires_permission(P_SUPERVISOR_VIEW))] = None,
    uow: Annotated[UnitOfWork, Depends(get_uow)] = None,
) -> dict[str, Any]:
    async with uow as u:
        q = select(RiskFlag).order_by(desc(RiskFlag.id)).limit(limit)
        if actor_type:
            q = q.where(RiskFlag.actor_type == actor_type)
        rows = (await u.session.execute(q)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "flag_code": r.flag_code,
                "actor_type": r.actor_type,
                "actor_id": r.actor_id,
                "reason": r.reason,
                "order_id": r.order_id,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/failed-delivery-reviews")
async def list_failed_delivery_reviews(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    _gate: Annotated[Principal, Depends(requires_permission(P_SUPERVISOR_VIEW))] = None,
    uow: Annotated[UnitOfWork, Depends(get_uow)] = None,
) -> dict[str, Any]:
    async with uow as u:
        q = select(FailedDeliveryReview).order_by(
            desc(FailedDeliveryReview.created_at)
        ).limit(limit)
        rows = (await u.session.execute(q)).scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "review_code": r.review_code,
                "order_id": r.order_id,
                "delivery_task_id": r.delivery_task_id,
                "decision": getattr(r, "decision", None),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


__all__ = ["router"]
