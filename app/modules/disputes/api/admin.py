"""Admin / mediator dispute endpoints — list, decide, escrow audit."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.disputes import repository as repo
from app.modules.disputes import service
from app.modules.disputes.schemas import (
    DisputeListResponse,
    DisputeMessageRead,
    DisputeRead,
    EscrowHoldListResponse,
    EscrowHoldRead,
    EvidenceListResponse,
    EvidenceRead,
    MediatorAssignRequest,
    MediatorDecisionRequest,
    MessageListResponse,
)

router = APIRouter(prefix="/admin/disputes", tags=["admin-disputes"])

_PERM_VIEW = "disputes.view"
_PERM_MANAGE = "disputes.manage"
_PERM_MEDIATE = "disputes.mediate"


@router.get(
    "",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List all disputes",
)
async def list_all(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    seller_id: Annotated[UUID | None, Query()] = None,
    mediator_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_disputes_admin(
            session,
            status=status_filter,
            seller_id=seller_id,
            mediator_id=mediator_id,
            limit=limit,
            offset=offset,
        )
    return DisputeListResponse(
        items=[DisputeRead.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")


@router.get(
    "/escrow/holds",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List active escrow holds",
)
async def list_escrow_holds(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    seller_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_active_escrow_holds(
            session, seller_id=seller_id, limit=limit, offset=offset,
        )
    return EscrowHoldListResponse(
        items=[EscrowHoldRead.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")


@router.get(
    "/{dispute_id}",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="Read a dispute (admin)",
)
async def read_one(
    dispute_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        d = await repo.get_dispute(session, dispute_id)
        if d is None:
            raise HTTPException(status_code=404, detail="Dispute not found.")
    return DisputeRead.model_validate(d).model_dump(mode="json")


@router.get(
    "/{dispute_id}/messages",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List messages on a dispute",
)
async def list_messages_admin(
    dispute_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        rows = await repo.list_messages(session, dispute_id)
    return MessageListResponse(
        items=[DisputeMessageRead.model_validate(r) for r in rows],
        total=len(rows),
    ).model_dump(mode="json")


@router.get(
    "/{dispute_id}/evidence",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List evidence on a dispute",
)
async def list_evidence_admin(
    dispute_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        rows = await repo.list_evidence(session, dispute_id)
    return EvidenceListResponse(
        items=[EvidenceRead.model_validate(r) for r in rows],
        total=len(rows),
    ).model_dump(mode="json")


@router.post(
    "/{dispute_id}/assign",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    status_code=status.HTTP_200_OK,
    summary="Assign mediator",
)
async def assign_mediator(
    dispute_id: UUID,
    body: MediatorAssignRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.assign_mediator(
                session,
                dispute_id=dispute_id,
                mediator_user_id=body.mediator_user_id,
                internal_note=body.internal_note,
            )
        except service.DisputeNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except service.DisputeAlreadyResolved as e:
            raise HTTPException(status_code=409, detail=str(e)) from e


@router.post(
    "/{dispute_id}/decide",
    dependencies=[Depends(requires_permission(_PERM_MEDIATE))],
    summary="Mediator final decision",
)
async def decide(
    dispute_id: UUID,
    body: MediatorDecisionRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.mediator_decide(
                session,
                dispute_id=dispute_id,
                mediator_user_id=principal.user_id,
                resolution=body.resolution,
                refund_amount_minor=body.refund_amount_minor,
                decision_notes=body.decision_notes,
            )
        except service.DisputeNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except service.DisputeAlreadyResolved as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except service.InvalidDisputeTransition as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except service.EscrowNotFound as e:
            raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/{dispute_id}/close",
    dependencies=[Depends(requires_permission(_PERM_MANAGE))],
    summary="Close a resolved dispute",
)
async def close(
    dispute_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.close_resolved_dispute(
                session,
                dispute_id=dispute_id,
                actor_user_id=principal.user_id,
            )
        except service.DisputeNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except service.InvalidDisputeTransition as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
