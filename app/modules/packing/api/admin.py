from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.packing.api._serializers import session_to_response
from app.modules.packing.repository import PackingRepository
from app.modules.packing.schemas import (
    CancelSessionRequest,
    OpenSessionRequest,
    OverrideScanRequest,
    PackingSessionResponse,
    ScanRequest,
    ScanResultResponse,
)
from app.modules.packing.service import PackingService

router = APIRouter(prefix="/admin/packing", tags=["admin-packing"])

# Picker actions reuse the staff fulfillment permission. Supervisor-only
# actions reuse iam.role.assign — when the IAM 7-role catalog ships, we
# split into dedicated `packing.scan` / `packing.override` permissions.
_PICKER = "order.fulfill"
_SUPERVISOR = "iam.role.assign"


@router.post(
    "/sessions",
    response_model=PackingSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a packing session for an order",
    description=(
        "Order must be in ``packing`` state. Snapshots the FEFO-chosen "
        "reserved batches into expected lines so the picker knows exactly "
        "what batch each unit should come from."
    ),
    dependencies=[Depends(requires_permission(_PICKER))],
)
async def open_session(
    payload: OpenSessionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    order_id: UUID | None = Query(default=None, description="Order id to open packing for"),
) -> PackingSessionResponse:
    # Accept order_id via either the ?order_id query param (canonical) OR
    # the JSON body for clients that prefer body-only payloads. Query wins
    # if both are supplied. Missing both → ValidationError (clear 400) not
    # a generic 422 schema error.
    from app.core.errors import ValidationError as DomainValidationError
    effective_order_id = order_id or payload.order_id
    if effective_order_id is None:
        raise DomainValidationError(
            "order_id is required (send via ?order_id=<uuid> query or {\"order_id\": ...} body).",
        )
    async with uow.transactional() as session:
        svc = PackingService(session)
        s = await svc.open_session(
            principal=principal, order_id=effective_order_id, notes=payload.notes,
        )
        return session_to_response(s)


@router.get(
    "/sessions",
    response_model=Page[PackingSessionResponse],
    dependencies=[Depends(requires_permission(_PICKER))],
)
async def list_sessions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
) -> Page[PackingSessionResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = PackingRepository(session)
        rows, total = await repo.list_admin(
            offset=params.offset, limit=params.limit, status=status_filter,
        )
    return Page.build(
        items=[session_to_response(s) for s in rows], total=total, params=params,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=PackingSessionResponse,
    dependencies=[Depends(requires_permission(_PICKER))],
)
async def get_session(
    session_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PackingSessionResponse:
    from app.core.errors import NotFoundError

    async with uow.transactional() as session:
        repo = PackingRepository(session)
        s = await repo.get_session(session_id)
        if s is None:
            raise NotFoundError("Packing session not found.")
        return session_to_response(s)


@router.post(
    "/sessions/{session_id}/scan",
    response_model=ScanResultResponse,
    summary="Picker scans a unit barcode + identifies the batch",
    description=(
        "Returns 200 in every outcome — the body's ``accepted`` flag and "
        "``outcome`` enum tell the picker what happened. ``can_supervisor_"
        "override = true`` means the picker should escalate (e.g. batch "
        "mismatch). Block outcomes (wrong_item, expired, unknown_barcode, "
        "over_quantity) cannot be overridden."
    ),
    dependencies=[Depends(requires_permission(_PICKER))],
)
async def scan(
    session_id: UUID,
    payload: ScanRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ScanResultResponse:
    async with uow.transactional() as session:
        svc = PackingService(session)
        result = await svc.scan(
            principal=principal,
            session_id=session_id,
            barcode=payload.barcode,
            batch_id=payload.batch_id,
            notes=payload.notes,
        )
        return ScanResultResponse(**result)


@router.post(
    "/sessions/{session_id}/override-scan",
    response_model=ScanResultResponse,
    summary="Supervisor approves a batch substitution",
    description=(
        "Used after a picker hits a ``batch_mismatch``. The supervisor "
        "(authenticated user) accepts a different batch for the line; the "
        "line's ``accepted_batch_id`` is recorded and a single accepted "
        "scan is registered. Refuses expired or blocked batches."
    ),
    dependencies=[Depends(requires_permission(_SUPERVISOR))],
)
async def override_scan(
    session_id: UUID,
    payload: OverrideScanRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ScanResultResponse:
    async with uow.transactional() as session:
        svc = PackingService(session)
        result = await svc.override_scan(
            principal=principal,
            session_id=session_id,
            line_id=payload.line_id,
            barcode=payload.barcode,
            batch_id=payload.batch_id,
            reason=payload.reason,
        )
        return ScanResultResponse(**result)


@router.post(
    "/sessions/{session_id}/cancel",
    response_model=PackingSessionResponse,
    summary="Cancel an open packing session",
    dependencies=[Depends(requires_permission(_SUPERVISOR))],
)
async def cancel_session(
    session_id: UUID,
    payload: CancelSessionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PackingSessionResponse:
    async with uow.transactional() as session:
        svc = PackingService(session)
        s = await svc.cancel_session(
            principal=principal, session_id=session_id, reason=payload.reason,
        )
        return session_to_response(s)
