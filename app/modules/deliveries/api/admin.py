from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.deliveries.api._serializers import (
    assignment_to_response,
    assignment_to_summary,
    rider_to_response,
)
from app.modules.deliveries.repository import (
    DeliveryAssignmentRepository,
    RiderRepository,
)
from app.modules.deliveries.schemas import (
    AssignDeliveryRequest,
    CancelAssignmentRequest,
    CodReconcileRequest,
    DeliveryAssignmentResponse,
    DeliveryAssignmentSummary,
    RiderCreate,
    RiderResponse,
    RiderUpdate,
)
from app.modules.deliveries.service import DeliveryService

router = APIRouter(prefix="/admin/deliveries", tags=["admin-deliveries"])

_RIDER_ADMIN = "iam.role.assign"
_DISPATCH = "order.fulfill"


# ---------------- Riders ----------------


@router.post(
    "/riders",
    response_model=RiderResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RIDER_ADMIN))],
)
async def create_rider(
    payload: RiderCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RiderResponse:
    fields = payload.model_dump()
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        r = await svc.create_rider(principal=principal, fields=fields)
        return rider_to_response(r)


@router.get(
    "/riders",
    response_model=list[RiderResponse],
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def list_riders(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    active_only: bool = Query(default=False),
) -> list[RiderResponse]:
    async with uow.transactional() as session:
        repo = RiderRepository(session)
        rows = await repo.list_all(active_only=active_only)
        return [rider_to_response(r) for r in rows]


@router.patch(
    "/riders/{rider_id}",
    response_model=RiderResponse,
    dependencies=[Depends(requires_permission(_RIDER_ADMIN))],
)
async def update_rider(
    rider_id: UUID,
    payload: RiderUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RiderResponse:
    fields = payload.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        r = await svc.update_rider(
            principal=principal, rider_id=rider_id, fields=fields,
        )
        return rider_to_response(r)


# ---------------- Assignment ----------------


@router.post(
    "/assignments",
    response_model=DeliveryAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign an OUT_FOR_DELIVERY order to a rider",
    description=(
        "Order must be in ``out_for_delivery`` state. Pre-fills "
        "``cod_expected`` from the order's grand total when "
        "``payment_method = cod``, else 0."
    ),
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def assign(
    payload: AssignDeliveryRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryAssignmentResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        a = await svc.assign(
            principal=principal,
            order_id=payload.order_id,
            rider_id=payload.rider_id,
            notes=payload.notes,
        )
        return assignment_to_response(a)


@router.get(
    "",
    response_model=Page[DeliveryAssignmentSummary],
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def admin_list(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    rider_id: UUID | None = Query(default=None),
) -> Page[DeliveryAssignmentSummary]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = DeliveryAssignmentRepository(session)
        rows, total = await repo.list_admin(
            offset=params.offset,
            limit=params.limit,
            status=status_filter,
            rider_id=rider_id,
        )
    return Page.build(
        items=[assignment_to_summary(a) for a in rows], total=total, params=params,
    )


@router.get(
    "/{assignment_id}",
    response_model=DeliveryAssignmentResponse,
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def admin_get(
    assignment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> DeliveryAssignmentResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        return assignment_to_response(await svc.get_admin(assignment_id))


@router.post(
    "/{assignment_id}/cancel",
    response_model=DeliveryAssignmentResponse,
    dependencies=[Depends(requires_permission(_RIDER_ADMIN))],
)
async def admin_cancel(
    assignment_id: UUID,
    payload: CancelAssignmentRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryAssignmentResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        a = await svc.cancel(
            principal=principal,
            assignment_id=assignment_id,
            reason=payload.reason,
        )
        return assignment_to_response(a)


@router.post(
    "/{assignment_id}/reconcile-cod",
    response_model=DeliveryAssignmentResponse,
    summary="Supervisor closes a COD discrepancy",
    description=(
        "Only valid when ``cod_status = discrepancy``. Records resolution "
        "notes and, if the delivery is already in DELIVERED state, "
        "advances it to COMPLETED — which fires the ``orders.order."
        "completed`` event the inventory module consumes to drain stock."
    ),
    dependencies=[Depends(requires_permission(_RIDER_ADMIN))],
)
async def admin_reconcile_cod(
    assignment_id: UUID,
    payload: CodReconcileRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryAssignmentResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        a = await svc.reconcile_cod(
            principal=principal,
            assignment_id=assignment_id,
            resolution_notes=payload.resolution_notes,
        )
        return assignment_to_response(a)
