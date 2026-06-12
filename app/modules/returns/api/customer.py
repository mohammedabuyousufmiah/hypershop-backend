from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.returns.api._serializers import to_response, to_summary
from app.modules.returns.repository import ReturnRepository
from app.modules.returns.schemas import (
    CancelReturnRequest,
    CreateReturnRequest,
    ReturnRequestResponse,
    ReturnRequestSummary,
)
from app.modules.returns.service import ReturnService

router = APIRouter(prefix="/returns", tags=["returns"])

_PLACE = "order.place"
_READ_SELF = "order.read.self"


@router.post(
    "",
    response_model=ReturnRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a return request for a completed order",
    dependencies=[Depends(requires_permission(_PLACE))],
)
async def request_return(
    payload: CreateReturnRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        rr = await svc.request(
            principal=principal,
            order_id=payload.order_id,
            reason=payload.reason,
            lines=[li.model_dump() for li in payload.lines],
            notes=payload.notes,
        )
        return to_response(rr)


@router.get(
    "",
    response_model=Page[ReturnRequestSummary],
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def list_mine(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
) -> Page[ReturnRequestSummary]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = ReturnRepository(session)
        rows, total = await repo.list_for_customer(
            customer_user_id=principal.user_id,
            offset=params.offset,
            limit=params.limit,
            status=status_filter,
        )
    return Page.build(items=[to_summary(r) for r in rows], total=total, params=params)


@router.get(
    "/{request_id}",
    response_model=ReturnRequestResponse,
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def get_mine(
    request_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        return to_response(
            await svc.get_for_customer(principal=principal, request_id=request_id),
        )


@router.post(
    "/{request_id}/cancel",
    response_model=ReturnRequestResponse,
    summary="Customer cancels a still-open return",
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def cancel_mine(
    request_id: UUID,
    payload: CancelReturnRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        rr = await svc.cancel_by_customer(
            principal=principal, request_id=request_id, reason=payload.reason,
        )
        return to_response(rr)
