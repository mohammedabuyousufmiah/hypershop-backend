"""Seller-facing dispute endpoints — list/read/reply/accept/counter."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.disputes import repository as repo
from app.modules.disputes import service
from app.modules.disputes.schemas import (
    DisputeListResponse,
    DisputeMessageCreate,
    DisputeMessageRead,
    DisputeRead,
    EvidenceListResponse,
    EvidenceRead,
    MessageListResponse,
    SellerCounterOfferRequest,
)
from app.modules.sellers.deps import get_current_seller_id

router = APIRouter(prefix="/seller/disputes", tags=["seller-disputes"])


async def _load_seller_dispute(session, dispute_id: UUID, seller_id: UUID):
    d = await repo.get_dispute(session, dispute_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    if d.seller_id != seller_id:
        raise HTTPException(status_code=403, detail="Not your dispute.")
    return d


@router.get("", summary="List disputes against me")
async def list_against_me(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_disputes_by_seller(
            session, seller_id,
            status=status_filter, limit=limit, offset=offset,
        )
    return DisputeListResponse(
        items=[DisputeRead.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")


@router.get("/{dispute_id}", summary="Read a dispute against me")
async def read_against_me(
    dispute_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        d = await _load_seller_dispute(session, dispute_id, seller_id)
    return DisputeRead.model_validate(d).model_dump(mode="json")


@router.post("/{dispute_id}/messages", summary="Reply to dispute")
async def reply(
    dispute_id: UUID,
    body: DisputeMessageCreate,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.seller_respond(
                session,
                dispute_id=dispute_id,
                seller_id=seller_id,
                body=body.body,
                attachments=body.attachments,
            )
        except service.DisputeNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except service.DisputeNotOwned as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except service.DisputeAlreadyResolved as e:
            raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/{dispute_id}/accept", summary="Accept full refund")
async def accept(
    dispute_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.seller_accept(
                session, dispute_id=dispute_id, seller_id=seller_id,
            )
        except service.DisputeNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except service.DisputeNotOwned as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except service.DisputeAlreadyResolved as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except service.EscrowNotFound as e:
            raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{dispute_id}/counter", summary="Propose partial refund")
async def counter(
    dispute_id: UUID,
    body: SellerCounterOfferRequest,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.seller_counter(
                session,
                dispute_id=dispute_id,
                seller_id=seller_id,
                counter_amount_minor=body.counter_amount_minor,
                message=body.message,
            )
        except service.DisputeNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except service.DisputeNotOwned as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except service.DisputeAlreadyResolved as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except service.InvalidDisputeTransition as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{dispute_id}/messages", summary="List messages")
async def list_messages_seller(
    dispute_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        await _load_seller_dispute(session, dispute_id, seller_id)
        rows = await repo.list_messages(session, dispute_id)
    return MessageListResponse(
        items=[DisputeMessageRead.model_validate(r) for r in rows],
        total=len(rows),
    ).model_dump(mode="json")


@router.get("/{dispute_id}/evidence", summary="List evidence")
async def list_evidence_seller(
    dispute_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        await _load_seller_dispute(session, dispute_id, seller_id)
        rows = await repo.list_evidence(session, dispute_id)
    return EvidenceListResponse(
        items=[EvidenceRead.model_validate(r) for r in rows],
        total=len(rows),
    ).model_dump(mode="json")
