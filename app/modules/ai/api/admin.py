"""AI admin endpoints.

Capability surfaces (require ``ai.use``):
- ``POST /admin/ai/predict-stock`` — JSON body
- ``POST /admin/ai/detect-fraud`` — JSON body

Proposal review (require ``ai.use``):
- ``POST /admin/ai/proposals/{id}/accept``
- ``POST /admin/ai/proposals/{id}/amend``
- ``POST /admin/ai/proposals/{id}/reject``

Read surface (require ``ai.read``):
- ``GET /admin/ai/proposals``
- ``GET /admin/ai/proposals/{id}``
- ``GET /admin/ai/usage``
- ``GET /admin/ai/status``
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
)

from app.core.db.uow import UnitOfWork, get_uow
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.ai.repository import (
    AIProposalRepository,
    AIUsageEventRepository,
)
from app.modules.ai.schemas import (
    AICapabilityStatus,
    AIProposalResponse,
    AIUsageEventResponse,
    DetectFraudRequest,
    PredictStockRequest,
    ProposalAcceptRequest,
    ProposalAmendRequest,
    ProposalRejectRequest,
)
from app.modules.ai.service import AIService

router = APIRouter(prefix="/admin/ai", tags=["admin-ai"])

_USE = "ai.use"
_READ = "ai.read"


# ---------------- Capability status ----------------


@router.get(
    "/status",
    response_model=AICapabilityStatus,
    summary="Which AI provider is bound + which capabilities are live",
    dependencies=[Depends(requires_permission(_READ))],
)
async def status_endpoint() -> AICapabilityStatus:
    return AICapabilityStatus(**AIService.capability_status())


# ---------------- Capabilities ----------------


@router.post(
    "/predict-stock",
    response_model=AIProposalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Forecast depletion + reorder qty for a variant",
    dependencies=[Depends(requires_permission(_USE))],
)
async def predict_stock(
    payload: PredictStockRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AIProposalResponse:
    async with uow.transactional() as session:
        svc = AIService(session)
        proposal = await svc.predict_stock(
            actor=principal,
            variant_id=payload.variant_id,
            horizon_days=payload.horizon_days,
            history_days=payload.history_days,
        )
        return AIProposalResponse.model_validate(proposal)


@router.post(
    "/detect-fraud",
    response_model=AIProposalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Risk-score an order — returns a draft proposal",
    description=(
        "**Assistive only.** AI may recommend ``allow`` / ``review`` / "
        "``block`` but cannot cancel the order. Cancellation requires "
        "``order.cancel.any`` and goes through the orders module."
    ),
    dependencies=[Depends(requires_permission(_USE))],
)
async def detect_fraud(
    payload: DetectFraudRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AIProposalResponse:
    async with uow.transactional() as session:
        svc = AIService(session)
        proposal = await svc.detect_fraud(
            actor=principal, order_id=payload.order_id,
        )
        return AIProposalResponse.model_validate(proposal)


# ---------------- Proposal review ----------------


@router.post(
    "/proposals/{proposal_id}/accept",
    response_model=AIProposalResponse,
    summary="Accept the AI proposal as-is (does NOT execute the action)",
    dependencies=[Depends(requires_permission(_USE))],
)
async def accept_proposal(
    proposal_id: UUID,
    payload: ProposalAcceptRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AIProposalResponse:
    async with uow.transactional() as session:
        svc = AIService(session)
        p = await svc.accept_proposal(
            principal=principal,
            proposal_id=proposal_id,
            notes=payload.notes,
        )
        return AIProposalResponse.model_validate(p)


@router.post(
    "/proposals/{proposal_id}/amend",
    response_model=AIProposalResponse,
    summary="Accept the AI proposal with edits (does NOT execute the action)",
    dependencies=[Depends(requires_permission(_USE))],
)
async def amend_proposal(
    proposal_id: UUID,
    payload: ProposalAmendRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AIProposalResponse:
    async with uow.transactional() as session:
        svc = AIService(session)
        p = await svc.amend_proposal(
            principal=principal,
            proposal_id=proposal_id,
            decision_payload=payload.decision_payload,
            notes=payload.notes,
        )
        return AIProposalResponse.model_validate(p)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=AIProposalResponse,
    dependencies=[Depends(requires_permission(_USE))],
)
async def reject_proposal(
    proposal_id: UUID,
    payload: ProposalRejectRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AIProposalResponse:
    async with uow.transactional() as session:
        svc = AIService(session)
        p = await svc.reject_proposal(
            principal=principal,
            proposal_id=proposal_id,
            reason=payload.reason,
        )
        return AIProposalResponse.model_validate(p)


# ---------------- Read ----------------


@router.get(
    "/proposals",
    response_model=Page[AIProposalResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_proposals(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    kind: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    requested_by: UUID | None = Query(default=None),
    reference_type: str | None = Query(default=None),
    reference_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=50, ge=1, le=200),
) -> Page[AIProposalResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = AIProposalRepository(session)
        rows, total = await repo.list_filtered(
            kind=kind,
            status=status_filter,
            requested_by=requested_by,
            reference_type=reference_type,
            reference_id=reference_id,
            offset=params.offset,
            limit=params.limit,
        )
    return Page.build(
        items=[AIProposalResponse.model_validate(p) for p in rows],
        total=total,
        params=params,
    )


@router.get(
    "/proposals/{proposal_id}",
    response_model=AIProposalResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_proposal(
    proposal_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AIProposalResponse:
    async with uow.transactional() as session:
        repo = AIProposalRepository(session)
        p = await repo.get(proposal_id)
        if p is None:
            from app.core.errors import NotFoundError
            raise NotFoundError("AI proposal not found.")
        return AIProposalResponse.model_validate(p)


@router.get(
    "/usage",
    response_model=Page[AIUsageEventResponse],
    summary="AI provider call ledger (cost + latency + success / failure)",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_usage(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    kind: str | None = Query(default=None),
    success: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=50, ge=1, le=200),
) -> Page[AIUsageEventResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = AIUsageEventRepository(session)
        rows, total = await repo.list_recent(
            kind=kind,
            success=success,
            offset=params.offset,
            limit=params.limit,
        )
    return Page.build(
        items=[AIUsageEventResponse.model_validate(r) for r in rows],
        total=total,
        params=params,
    )
