"""Rider-app facing endpoints.

Permission model: every endpoint requires the ``rider.app`` permission
which is granted to the ``rider`` role at IAM bootstrap. The service
layer additionally enforces that the rider can only mutate stops on
their own run sheet.

Convention: the endpoints take ``rider_id`` in the body so the same
backend can support a future "ops impersonate rider" flow without an
auth flag. For ride-of-record actions (ingest_location, start/end
shift) this should match ``principal.user_id`` — enforced in the
handlers.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ForbiddenError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.rider_routing.errors import (
    NoCandidateStopsError,
    RunSheetNotFoundError,
)
from app.modules.rider_routing.repository import (
    RiderLiveLocationRepository,
    RunSheetRepository,
    RunSheetStopRepository,
)
from app.modules.rider_routing.schemas import (
    CurrentRunSheetResponse,
    LocationUpdateRequest,
    LocationUpdateResponse,
    NextStopReason,
    NextStopResponse,
    RunSheetResponse,
    ShiftEndRequest,
    ShiftResponse,
    ShiftStartRequest,
    StopActionRequest,
    StopCompleteRequest,
    StopFailRequest,
    StopRescheduleRequest,
    StopResponse,
    StopSummary,
)
from app.modules.rider_routing.service import RiderRoutingService
from app.modules.rider_routing.state import (
    RecalcTrigger,
    RunSheetStatus,
    ShiftStatus,
)

router = APIRouter(prefix="/rider", tags=["rider"])


async def _enforce_self(
    *,
    session,
    principal: Principal,
    body_rider_id: UUID,
) -> None:
    """A rider can only act on their own Rider record.

    Looks up the Rider by ``body_rider_id`` and verifies that
    ``Rider.linked_user_id`` equals the authenticated principal. A
    superuser (wildcard permission) bypasses the check so ops can
    impersonate-test the rider flow.
    """
    if principal.has_permission("*"):
        return
    from app.modules.deliveries.models import Rider
    rider = await session.get(Rider, body_rider_id)
    if rider is None:
        raise ForbiddenError("Rider not found or not bound to a user.")
    if rider.linked_user_id is None or rider.linked_user_id != principal.user_id:
        raise ForbiddenError(
            "Rider self-service: rider_id is not bound to the "
            "authenticated user.",
        )


# ----------------------------------------------------------------------
# Shift
# ----------------------------------------------------------------------
@router.post(
    "/shifts/start",
    response_model=ShiftResponse,
    summary="Clock in: open a new shift for the current rider",
)
async def start_shift(
    body: ShiftStartRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ShiftResponse:
    async with uow.transactional() as session:
        await _enforce_self(
            session=session, principal=principal, body_rider_id=body.rider_id,
        )
        svc = RiderRoutingService(session)
        row = await svc.start_shift(
            rider_id=body.rider_id,
            latitude=body.latitude,
            longitude=body.longitude,
            principal=principal,
            notes=body.notes,
        )
    return _shift_to_response(row)


@router.post(
    "/shifts/{shift_id}/pause",
    response_model=ShiftResponse,
    summary="Pause a started shift (still accepts location pings)",
)
async def pause_shift(
    shift_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ShiftResponse:
    async with uow.transactional() as session:
        svc = RiderRoutingService(session)
        row = await svc.transition_shift(
            shift_id=shift_id,
            to_status=ShiftStatus.PAUSED,
            principal=principal,
        )
    return _shift_to_response(row)


@router.post(
    "/shifts/{shift_id}/resume",
    response_model=ShiftResponse,
    summary="Resume a paused shift",
)
async def resume_shift(
    shift_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ShiftResponse:
    async with uow.transactional() as session:
        svc = RiderRoutingService(session)
        row = await svc.transition_shift(
            shift_id=shift_id,
            to_status=ShiftStatus.STARTED,
            principal=principal,
        )
    return _shift_to_response(row)


@router.post(
    "/shifts/{shift_id}/end",
    response_model=ShiftResponse,
    summary="End the shift; final location optional but recommended",
)
async def end_shift(
    shift_id: UUID,
    body: ShiftEndRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ShiftResponse:
    async with uow.transactional() as session:
        svc = RiderRoutingService(session)
        row = await svc.transition_shift(
            shift_id=shift_id,
            to_status=ShiftStatus.ENDED,
            principal=principal,
            end_latitude=body.end_latitude,
            end_longitude=body.end_longitude,
        )
    return _shift_to_response(row)


# ----------------------------------------------------------------------
# Location
# ----------------------------------------------------------------------
@router.post(
    "/location/update",
    response_model=LocationUpdateResponse,
    summary="Ingest a single GPS ping (rider app calls every ~20s while active)",
)
async def update_location(
    body: LocationUpdateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> LocationUpdateResponse:
    async with uow.transactional() as session:
        await _enforce_self(
            session=session, principal=principal, body_rider_id=body.rider_id,
        )
        svc = RiderRoutingService(session)
        row = await svc.ingest_location(
            rider_id=body.rider_id,
            latitude=body.latitude,
            longitude=body.longitude,
            recorded_at=body.recorded_at,
            accuracy_meters=body.accuracy_meters,
            speed_kmh=body.speed_kmh,
            heading_degrees=body.heading_degrees,
            battery_level=body.battery_level,
            is_mocked=body.is_mocked,
            principal=principal,
        )
    return LocationUpdateResponse(
        accepted=True,
        next_recompute_in_seconds=30,
        last_known_at=row.recorded_at,
    )


# ----------------------------------------------------------------------
# Run sheet
# ----------------------------------------------------------------------
@router.get(
    "/run-sheet/current",
    response_model=CurrentRunSheetResponse,
    summary="Get the rider's currently active run sheet (assigned or in_progress)",
)
async def current_run_sheet(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CurrentRunSheetResponse:
    async with uow.transactional() as session:
        run_sheets = RunSheetRepository(session)
        stops_repo = RunSheetStopRepository(session)
        sheet = await run_sheets.get_active_for_rider(principal.user_id)
        if sheet is None:
            return CurrentRunSheetResponse(run_sheet=None, next_stop_id=None)
        stops = await stops_repo.list_for_sheet(sheet.id, order="dynamic")
        next_stop = await stops_repo.get_current_next(sheet.id)
        return CurrentRunSheetResponse(
            run_sheet=_run_sheet_to_response(sheet, stops),
            next_stop_id=next_stop.id if next_stop else None,
        )


@router.post(
    "/run-sheet/{run_sheet_id}/recommendation/refresh",
    response_model=NextStopResponse,
    summary="Manually request a next-stop recompute",
)
async def refresh_recommendation(
    run_sheet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> NextStopResponse:
    async with uow.transactional() as session:
        svc = RiderRoutingService(session)
        try:
            result, changed = await svc.recommend_next_stop(
                run_sheet_id=run_sheet_id,
                principal=principal,
                trigger=RecalcTrigger.MANUAL_REFRESH,
            )
        except NoCandidateStopsError:
            return NextStopResponse(
                chosen=None, alternates=[], candidates_considered=0,
                changed=False,
            )
    return NextStopResponse(
        chosen=_score_to_reason(result.chosen),
        alternates=[_score_to_reason(a) for a in result.alternates if a],
        candidates_considered=result.candidates_considered,
        changed=changed,
    )


# ----------------------------------------------------------------------
# Stops
# ----------------------------------------------------------------------
@router.post(
    "/stops/{stop_id}/start",
    response_model=StopResponse,
    summary="Start a stop: rider has begun heading there",
)
async def start_stop(
    stop_id: UUID,
    body: StopActionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> StopResponse:
    async with uow.transactional() as session:
        await _enforce_self(
            session=session, principal=principal, body_rider_id=body.rider_id,
        )
        svc = RiderRoutingService(session)
        stop = await svc.start_stop(
            stop_id=stop_id,
            rider_id=body.rider_id,
            principal=principal,
        )
    return _stop_to_response(stop)


@router.post(
    "/stops/{stop_id}/arrive",
    response_model=StopResponse,
    summary="Arrived at the customer's location",
)
async def arrive_stop(
    stop_id: UUID,
    body: StopActionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> StopResponse:
    async with uow.transactional() as session:
        await _enforce_self(
            session=session, principal=principal, body_rider_id=body.rider_id,
        )
        svc = RiderRoutingService(session)
        stop = await svc.arrive_stop(
            stop_id=stop_id,
            rider_id=body.rider_id,
            principal=principal,
        )
    return _stop_to_response(stop)


@router.post(
    "/stops/{stop_id}/complete",
    response_model=StopResponse,
    summary="Mark the stop delivered (POD must already be on the assignment)",
)
async def complete_stop(
    stop_id: UUID,
    body: StopCompleteRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> StopResponse:
    async with uow.transactional() as session:
        await _enforce_self(
            session=session, principal=principal, body_rider_id=body.rider_id,
        )
        svc = RiderRoutingService(session)
        stop = await svc.complete_stop(
            stop_id=stop_id,
            rider_id=body.rider_id,
            principal=principal,
            cash_collected_bdt=body.cash_collected_bdt,
        )
    return _stop_to_response(stop)


@router.post(
    "/stops/{stop_id}/fail",
    response_model=StopResponse,
    summary="Mark the stop as failed (with a reason)",
)
async def fail_stop(
    stop_id: UUID,
    body: StopFailRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> StopResponse:
    async with uow.transactional() as session:
        await _enforce_self(
            session=session, principal=principal, body_rider_id=body.rider_id,
        )
        svc = RiderRoutingService(session)
        stop = await svc.fail_stop(
            stop_id=stop_id,
            rider_id=body.rider_id,
            principal=principal,
            reason=body.reason,
        )
    return _stop_to_response(stop)


@router.post(
    "/stops/{stop_id}/reschedule",
    response_model=StopResponse,
    summary="Push the stop back to the queue with a new time window",
)
async def reschedule_stop(
    stop_id: UUID,
    body: StopRescheduleRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> StopResponse:
    async with uow.transactional() as session:
        await _enforce_self(
            session=session, principal=principal, body_rider_id=body.rider_id,
        )
        svc = RiderRoutingService(session)
        stop = await svc.reschedule_stop(
            stop_id=stop_id,
            rider_id=body.rider_id,
            principal=principal,
            promised_start_at=body.promised_start_at,
            promised_end_at=body.promised_end_at,
        )
    return _stop_to_response(stop)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _shift_to_response(row) -> ShiftResponse:
    return ShiftResponse(
        id=row.id,
        rider_id=row.rider_id,
        shift_date=row.shift_date,
        status=row.status,
        started_at=row.started_at,
        ended_at=row.ended_at,
        start_latitude=row.start_latitude,
        start_longitude=row.start_longitude,
        end_latitude=row.end_latitude,
        end_longitude=row.end_longitude,
    )


def _stop_to_response(row) -> StopResponse:
    return StopResponse(
        id=row.id,
        run_sheet_id=row.run_sheet_id,
        delivery_assignment_id=row.delivery_assignment_id,
        stop_status=row.stop_status,
        attempt_count=row.attempt_count,
        last_recomputed_at=row.last_recomputed_at,
    )


def _run_sheet_to_response(sheet, stops) -> RunSheetResponse:
    return RunSheetResponse(
        id=sheet.id,
        rider_id=sheet.rider_id,
        shift_id=sheet.shift_id,
        sheet_date=sheet.sheet_date,
        status=sheet.status,
        planned_stop_count=sheet.planned_stop_count,
        completed_stop_count=sheet.completed_stop_count,
        failed_stop_count=sheet.failed_stop_count,
        total_cod_amount=sheet.total_cod_amount,
        sequence_frozen=sheet.sequence_frozen,
        notes=sheet.notes,
        stops=[
            StopSummary(
                id=s.id,
                delivery_assignment_id=s.delivery_assignment_id,
                planned_sequence=s.planned_sequence,
                dynamic_sequence=s.dynamic_sequence,
                stop_status=s.stop_status,
                priority_level=s.priority_level,
                order_type_tag=s.order_type_tag,
                promised_start_at=s.promised_start_at,
                promised_end_at=s.promised_end_at,
                estimated_travel_time_min=s.estimated_travel_time_min,
                estimated_distance_km=s.estimated_distance_km,
                cash_to_collect_bdt=s.cash_to_collect_bdt,
                requires_otp=s.requires_otp,
                requires_signature=s.requires_signature,
                customer_available_flag=s.customer_available_flag,
                attempt_count=s.attempt_count,
                destination_latitude=s.destination_latitude,
                destination_longitude=s.destination_longitude,
                delivery_zone_code=s.delivery_zone_code,
            )
            for s in stops
        ],
    )


def _score_to_reason(bd) -> NextStopReason | None:
    if bd is None:
        return None
    return NextStopReason(
        stop_id=bd.stop_id,
        score=bd.score,
        eta_minutes=bd.eta_minutes,
        distance_km=bd.distance_km,
        reasons=bd.reasons,
    )
