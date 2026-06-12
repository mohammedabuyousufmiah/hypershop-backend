"""Admin fraud assessment endpoints."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.fraud.schemas import (
    FraudAssessmentListOut,
    FraudAssessmentOut,
    FraudCreateIn,
    FraudDecisionIn,
)
from app.modules.fraud.service import FraudService

router = APIRouter(prefix="/admin/fraud", tags=["admin-fraud"])

_R = "dashboard.read"
_W = "fraud.write"


@router.get(
    "/assessments",
    response_model=FraudAssessmentListOut,
    dependencies=[Depends(requires_permission(_R))],
)
async def list_assessments(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    decision: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> FraudAssessmentListOut:
    async with uow.transactional() as session:
        svc = FraudService(session)
        items, total = await svc.list_all(
            decision=decision,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
    return FraudAssessmentListOut(
        items=[FraudAssessmentOut.model_validate(a) for a in items],
        total=total,
    )


@router.post(
    "/assessments",
    response_model=FraudAssessmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_W))],
)
async def create_assessment(
    body: FraudCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> FraudAssessmentOut:
    async with uow.transactional() as session:
        svc = FraudService(session)
        row = await svc.create(
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            risk_score=body.risk_score,
            decision=body.decision,
            signals=body.signals,
            decision_reason=body.decision_reason,
            decided_by=principal.user_id,
        )
    return FraudAssessmentOut.model_validate(row)


@router.post(
    "/assessments/{assess_id}/decide",
    response_model=FraudAssessmentOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def decide_assessment(
    assess_id: UUID,
    body: FraudDecisionIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> FraudAssessmentOut:
    async with uow.transactional() as session:
        svc = FraudService(session)
        row = await svc.decide(
            assess_id=assess_id,
            decision=body.decision,
            decision_reason=body.decision_reason,
            actor_user_id=principal.user_id,
        )
    return FraudAssessmentOut.model_validate(row)
