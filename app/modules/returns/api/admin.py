from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.returns.api._serializers import to_response, to_summary
from app.modules.returns.repository import ReturnRepository
from app.modules.returns.schemas import (
    CancelReturnRequest,
    InspectReturnRequest,
    ReceiveRequest,
    RejectReturnRequest,
    ReturnRequestResponse,
    ReturnRequestSummary,
)
from app.modules.returns.service import ReturnService

router = APIRouter(prefix="/admin/returns", tags=["admin-returns"])

_READ = "order.read.any"
_FULFILL = "order.fulfill"
_CANCEL = "order.cancel.any"


@router.get(
    "",
    response_model=Page[ReturnRequestSummary],
    dependencies=[Depends(requires_permission(_READ))],
)
async def admin_list(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    order_id: UUID | None = Query(default=None),
) -> Page[ReturnRequestSummary]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = ReturnRepository(session)
        rows, total = await repo.list_admin(
            offset=params.offset,
            limit=params.limit,
            status=status_filter,
            order_id=order_id,
        )
    return Page.build(items=[to_summary(r) for r in rows], total=total, params=params)


@router.get(
    "/{request_id}",
    response_model=ReturnRequestResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def admin_get(
    request_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        return to_response(await svc.get_admin(request_id))


@router.post(
    "/{request_id}/receive",
    response_model=ReturnRequestResponse,
    summary="Mark the package as received at the warehouse",
    dependencies=[Depends(requires_permission(_FULFILL))],
)
async def admin_receive(
    request_id: UUID,
    payload: ReceiveRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        rr = await svc.receive(
            principal=principal, request_id=request_id, notes=payload.notes,
        )
        return to_response(rr)


@router.post(
    "/{request_id}/inspect",
    response_model=ReturnRequestResponse,
    summary="Record per-line condition + target batch + quantity",
    description=(
        "Every return line must be inspected exactly once with a "
        "``condition``, target ``batch_id`` and ``warehouse_code``. "
        "Conditions: ``sealed`` → restock to available; ``opened`` / "
        "``cold_chain_broken`` → block; ``expired`` → dispose."
    ),
    dependencies=[Depends(requires_permission(_FULFILL))],
)
async def admin_inspect(
    request_id: UUID,
    payload: InspectReturnRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        rr = await svc.inspect(
            principal=principal,
            request_id=request_id,
            line_inspections=[li.model_dump() for li in payload.lines],
        )
        return to_response(rr)


@router.post(
    "/{request_id}/complete",
    response_model=ReturnRequestResponse,
    summary="Apply inventory movements based on inspection conditions",
    description=(
        "Writes one ``return``-kind ledger row per line: ``+available`` for "
        "sealed; ``+blocked`` for opened / cold_chain_broken; "
        "``+expired`` for expired (disposal write-off captured in inventory "
        "history). Terminal."
    ),
    dependencies=[Depends(requires_permission(_FULFILL))],
)
async def admin_complete(
    request_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        rr = await svc.complete(principal=principal, request_id=request_id)
        return to_response(rr)


@router.post(
    "/{request_id}/reject",
    response_model=ReturnRequestResponse,
    dependencies=[Depends(requires_permission(_CANCEL))],
)
async def admin_reject(
    request_id: UUID,
    payload: RejectReturnRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        rr = await svc.reject(
            principal=principal, request_id=request_id, reason=payload.reason,
        )
        return to_response(rr)


@router.post(
    "/{request_id}/cancel",
    response_model=ReturnRequestResponse,
    dependencies=[Depends(requires_permission(_CANCEL))],
)
async def admin_cancel(
    request_id: UUID,
    payload: CancelReturnRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReturnRequestResponse:
    async with uow.transactional() as session:
        svc = ReturnService(session)
        rr = await svc.cancel_by_admin(
            principal=principal, request_id=request_id, reason=payload.reason,
        )
        return to_response(rr)
