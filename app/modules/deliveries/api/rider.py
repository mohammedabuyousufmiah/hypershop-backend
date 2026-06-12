from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ValidationError
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.deliveries.api._serializers import (
    assignment_to_response,
    assignment_to_summary,
)
from app.modules.deliveries.repository import DeliveryAssignmentRepository
from app.modules.deliveries.schemas import (
    DeliveryAssignmentResponse,
    DeliveryAssignmentSummary,
    DeliverRequest,
    FailAssignmentRequest,
    PickupRequest,
    RiderAvailabilityRequest,
    RiderCodSummaryResponse,
    RiderScanVerifyRequest,
    RiderScanVerifyResponse,
    RiderTaskItem,
    RiderTaskListResponse,
)
from app.modules.deliveries.service import DeliveryService

router = APIRouter(prefix="/rider/me/deliveries", tags=["rider-deliveries"])

_RIDER = "order.fulfill"


@router.get(
    "",
    response_model=Page[DeliveryAssignmentSummary],
    summary="List the calling rider's assignments",
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def list_mine(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
) -> Page[DeliveryAssignmentSummary]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        repo = DeliveryAssignmentRepository(session)
        rows, total = await repo.list_for_rider(
            rider_id=rider.id,
            offset=params.offset,
            limit=params.limit,
            status=status_filter,
        )
    return Page.build(
        items=[assignment_to_summary(a) for a in rows], total=total, params=params,
    )


@router.get(
    "/{assignment_id}",
    response_model=DeliveryAssignmentResponse,
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def get_mine(
    assignment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryAssignmentResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        return assignment_to_response(
            await svc.get_for_rider(rider=rider, assignment_id=assignment_id),
        )


@router.post(
    "/{assignment_id}/pickup",
    response_model=DeliveryAssignmentResponse,
    summary="Rider marks parcel picked up — `assigned → picked_up`",
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def pickup(
    assignment_id: UUID,
    payload: PickupRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryAssignmentResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        a = await svc.pickup(
            principal=principal,
            rider=rider,
            assignment_id=assignment_id,
            notes=payload.notes,
        )
        return assignment_to_response(a)


@router.post(
    "/{assignment_id}/upload-pod",
    response_model=DeliveryAssignmentResponse,
    summary="Upload a POD photo (JPG/PNG/WEBP)",
    description=(
        "Saves the photo as the POD evidence for this assignment. May be "
        "called multiple times — each call overwrites the previous photo. "
        "Must be called *before* ``deliver`` if relying on a photo (rather "
        "than OTP) for POD."
    ),
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def upload_pod(
    assignment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    file: Annotated[UploadFile, File(...)],
) -> DeliveryAssignmentResponse:
    if file.content_type is None:
        raise ValidationError("Missing content type on uploaded POD photo.")
    file_bytes = await file.read()
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        a = await svc.upload_pod_photo(
            principal=principal,
            rider=rider,
            assignment_id=assignment_id,
            file_bytes=file_bytes,
            mime=file.content_type,
        )
        return assignment_to_response(a)


@router.post(
    "/{assignment_id}/deliver",
    response_model=DeliveryAssignmentResponse,
    summary="Mark delivered — POD + (COD reconciliation if applicable) → completed",
    description=(
        "Hard rules: POD evidence is mandatory (photo via /upload-pod or "
        "``pod_otp_verified=true``); for COD orders ``cod_collected`` is "
        "required. If COD reconciles within tolerance the delivery "
        "auto-completes; if it discrepancies the delivery sits in "
        "DELIVERED until a supervisor calls ``/admin/deliveries/{id}/"
        "reconcile-cod``."
    ),
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def deliver(
    assignment_id: UUID,
    payload: DeliverRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryAssignmentResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        a = await svc.deliver(
            principal=principal,
            rider=rider,
            assignment_id=assignment_id,
            recipient_name=payload.recipient_name,
            pod_otp_verified=payload.pod_otp_verified,
            cod_collected=payload.cod_collected,
            notes=payload.notes,
        )
        return assignment_to_response(a)


@router.post(
    "/{assignment_id}/fail",
    response_model=DeliveryAssignmentResponse,
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def fail(
    assignment_id: UUID,
    payload: FailAssignmentRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeliveryAssignmentResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        a = await svc.fail(
            principal=principal,
            rider=rider,
            assignment_id=assignment_id,
            reason=payload.reason,
        )
        return assignment_to_response(a)


# ════════════════════════════════════════════════════════════════════════
# Rider mobile app endpoints (Module 18)
# ════════════════════════════════════════════════════════════════════════


def _task_item(a, order_code: str, addr: dict) -> RiderTaskItem:
    """Tight projection of an assignment for the rider's list screen."""
    return RiderTaskItem(
        assignment_id=a.id,
        order_id=a.order_id,
        order_code=order_code,
        status=a.status,
        payment_method=a.payment_method,
        cod_expected=a.cod_expected,
        cod_collected=a.cod_collected,
        cod_status=a.cod_status,
        recipient_name=str(addr.get("recipient_name") or "") or None,
        recipient_phone=str(addr.get("phone") or "") or None,
        address_line1=str(addr.get("line1") or "") or None,
        city=str(addr.get("city") or "") or None,
        assigned_at=a.assigned_at,
        picked_up_at=a.picked_up_at,
        delivered_at=a.delivered_at,
    )


@router.post(
    "/availability",
    response_model=dict[str, str],
    summary="Toggle rider availability (offline / available / busy)",
    description=(
        "Riders self-toggle. Going offline while any assignment is in "
        "ASSIGNED / PICKED_UP / DELIVERED state is rejected — the rider "
        "must hand the assignment back to dispatch first."
    ),
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def set_availability(
    payload: RiderAvailabilityRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, str]:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        rider = await svc.set_rider_availability(
            principal=principal, rider=rider, status_value=payload.status,
        )
    return {"rider_id": str(rider.id), "status": rider.current_status}


@router.get(
    "/tasks",
    response_model=RiderTaskListResponse,
    summary="Today's tasks for the calling rider",
    description=(
        "Route-friendly queue: in-flight pickups first, then pending "
        "pickups, then delivered (awaiting COD reconciliation), then "
        "anything terminal that closed today."
    ),
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def list_today_tasks(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RiderTaskListResponse:
    from app.modules.orders.models import Order

    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        rows = await svc.list_today_tasks(rider=rider)
        # Enrich with order code + customer address (already JSONB on order).
        items: list[RiderTaskItem] = []
        for a in rows:
            order = await session.get(Order, a.order_id)
            code = order.code if order else "—"
            addr = (order.delivery_address if order else {}) or {}
            items.append(_task_item(a, code, addr))
        counts: dict[str, int] = {}
        for a in rows:
            counts[a.status] = counts.get(a.status, 0) + 1
    return RiderTaskListResponse(
        rider_id=rider.id, items=items, counts=counts,
    )


@router.get(
    "/tasks/next",
    response_model=RiderTaskItem | None,
    summary="The single next task to act on (null when idle)",
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def get_next_task(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RiderTaskItem | None:
    from app.modules.orders.models import Order

    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        a = await svc.get_next_task(rider=rider)
        if a is None:
            return None
        order = await session.get(Order, a.order_id)
        return _task_item(
            a,
            order.code if order else "—",
            (order.delivery_address if order else {}) or {},
        )


@router.post(
    "/{assignment_id}/scan",
    response_model=RiderScanVerifyResponse,
    summary="Verify a parcel barcode scan against the assignment's order code",
    description=(
        "**Always returns 200** with ``ok: bool`` so the rider app can "
        "show a clear pass/fail toast and let the rider re-scan immediately "
        "on a noisy scan. The mismatch is audited regardless. Pass "
        "``intent='pickup'`` at warehouse or ``intent='delivery'`` at the "
        "doorstep — both compare against the same order code (printed on "
        "the parcel label)."
    ),
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def scan_verify(
    assignment_id: UUID,
    payload: RiderScanVerifyRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RiderScanVerifyResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        result = await svc.scan_verify(
            principal=principal,
            rider=rider,
            assignment_id=assignment_id,
            scanned_code=payload.scanned_code,
            intent=payload.intent,
        )
    return RiderScanVerifyResponse(**result)


@router.post(
    "/{assignment_id}/upload-signature",
    response_model=DeliveryAssignmentResponse,
    summary="Upload a recipient signature image (JPG/PNG/WEBP) as POD evidence",
    description=(
        "Companion to ``/upload-pod`` (which is a photo). Each call "
        "overwrites any previous signature for the same assignment. "
        "Either signature OR photo OR ``pod_otp_verified=true`` "
        "satisfies the POD-mandatory rule on ``/deliver``."
    ),
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def upload_signature(
    assignment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    file: Annotated[UploadFile, File(...)],
) -> DeliveryAssignmentResponse:
    if file.content_type is None:
        raise ValidationError("Missing content type on uploaded signature.")
    file_bytes = await file.read()
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        a = await svc.upload_pod_signature(
            principal=principal,
            rider=rider,
            assignment_id=assignment_id,
            file_bytes=file_bytes,
            mime=file.content_type,
        )
        return assignment_to_response(a)


@router.get(
    "/cod-summary",
    response_model=RiderCodSummaryResponse,
    summary="Cash the rider should be holding right now + today's collections",
    description=(
        "Reports the same per-rider numbers that admin finance sees "
        "(``expected_total``, ``deposited_total``, ``outstanding``) plus "
        "today's COD-collection count + amount as context for the rider's "
        "wallet screen."
    ),
    dependencies=[Depends(requires_permission(_RIDER))],
)
async def cod_summary(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RiderCodSummaryResponse:
    async with uow.transactional() as session:
        svc = DeliveryService(session)
        rider = await svc.rider_for_user(principal.user_id)
        summary = await svc.rider_cod_summary(rider=rider)
    return RiderCodSummaryResponse(**summary)
