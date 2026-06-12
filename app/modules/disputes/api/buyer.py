"""Buyer-facing dispute endpoints — open, reply, evidence, withdraw."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.modules.disputes import repository as repo
from app.modules.disputes import service
from app.modules.disputes.codes import ROLE_BUYER
from app.modules.disputes.schemas import (
    BuyerWithdrawRequest,
    DisputeListResponse,
    DisputeMessageCreate,
    DisputeMessageRead,
    DisputeOpenRequest,
    DisputeRead,
    EvidenceListResponse,
    EvidenceRead,
    EvidenceUploadRequest,
    MessageListResponse,
)

router = APIRouter(prefix="/disputes", tags=["disputes-buyer"])


def _to_dispute_read(d) -> DisputeRead:
    return DisputeRead.model_validate(d)


async def _load_owned_dispute(session, dispute_id: UUID, buyer_user_id: UUID):
    d = await repo.get_dispute(session, dispute_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Dispute not found.")
    if d.opened_by_user_id != buyer_user_id:
        raise HTTPException(status_code=403, detail="Not your dispute.")
    return d


@router.post("", status_code=status.HTTP_201_CREATED, summary="Open a dispute")
async def open_dispute(
    body: DisputeOpenRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.open_dispute(
                session,
                buyer_user_id=principal.user_id,
                order_id=body.order_id,
                order_item_id=body.order_item_id,
                dispute_type=body.dispute_type,
                subject=body.subject,
                description=body.description,
                amount_disputed_minor=body.amount_disputed_minor,
                seller_id=body.seller_id,
            )
        except service.InvalidDisputeTransition as e:
            raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("", summary="List my disputes")
async def list_mine(
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_disputes_by_buyer(
            session, principal.user_id,
            status=status_filter, limit=limit, offset=offset,
        )
    return DisputeListResponse(
        items=[_to_dispute_read(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")


@router.get("/{dispute_id}", summary="Read my dispute")
async def read_mine(
    dispute_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        d = await _load_owned_dispute(session, dispute_id, principal.user_id)
    return _to_dispute_read(d).model_dump(mode="json")


@router.post("/{dispute_id}/messages", summary="Reply to my dispute")
async def reply_mine(
    dispute_id: UUID,
    body: DisputeMessageCreate,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.buyer_respond(
                session,
                dispute_id=dispute_id,
                buyer_user_id=principal.user_id,
                body=body.body,
                attachments=body.attachments,
            )
        except service.DisputeNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except service.DisputeNotOwned as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except service.DisputeAlreadyResolved as e:
            raise HTTPException(status_code=409, detail=str(e)) from e


@router.post(
    "/{dispute_id}/evidence",
    status_code=status.HTTP_201_CREATED,
    summary="Register evidence (pre-uploaded URL)",
)
async def upload_evidence(
    dispute_id: UUID,
    body: EvidenceUploadRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        await _load_owned_dispute(session, dispute_id, principal.user_id)
        try:
            return await service.add_evidence(
                session,
                dispute_id=dispute_id,
                uploaded_by_user_id=principal.user_id,
                uploader_role=ROLE_BUYER,
                file_url=body.file_url,
                content_type=body.content_type,
                size_bytes=body.size_bytes,
                description=body.description,
            )
        except service.EvidenceLimitExceeded as e:
            raise HTTPException(status_code=413, detail=str(e)) from e


@router.get("/{dispute_id}/messages", summary="List messages on my dispute")
async def list_my_messages(
    dispute_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        await _load_owned_dispute(session, dispute_id, principal.user_id)
        rows = await repo.list_messages(session, dispute_id)
    return MessageListResponse(
        items=[DisputeMessageRead.model_validate(r) for r in rows],
        total=len(rows),
    ).model_dump(mode="json")


@router.get("/{dispute_id}/evidence", summary="List evidence on my dispute")
async def list_my_evidence(
    dispute_id: UUID,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        await _load_owned_dispute(session, dispute_id, principal.user_id)
        rows = await repo.list_evidence(session, dispute_id)
    return EvidenceListResponse(
        items=[EvidenceRead.model_validate(r) for r in rows],
        total=len(rows),
    ).model_dump(mode="json")


@router.post("/{dispute_id}/withdraw", summary="Withdraw my dispute")
async def withdraw(
    dispute_id: UUID,
    body: BuyerWithdrawRequest,
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.buyer_withdraw(
                session,
                dispute_id=dispute_id,
                buyer_user_id=principal.user_id,
                reason=body.reason,
            )
        except service.DisputeNotFound as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except service.DisputeNotOwned as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except service.DisputeAlreadyResolved as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except service.EscrowNotFound as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
