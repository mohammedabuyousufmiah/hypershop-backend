"""Admin/dispatch endpoints for rider routing.

Mounted at ``/admin/rider-dispatch``. Requires ``rider.dispatch``
permission (granted to admin + super_admin + ops at IAM bootstrap).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.time import utc_now
from app.modules.rider_routing.errors import RunSheetNotFoundError
from app.modules.rider_routing.override_service import OpsRouteOverrideService
from app.modules.rider_routing.repository import (
    OpsRouteOverrideRepository,
    RiderLiveLocationRepository,
    RouteRecalculationLogRepository,
    RunSheetRepository,
    RunSheetStopRepository,
)
from app.modules.rider_routing.schemas import (
    CreateRunSheetRequest,
    ForceNextStopRequest,
    FreezeRequest,
    LiveMapEntry,
    LiveMapResponse,
    OverrideListResponse,
    OverrideResponse,
    ReassignStopRequest,
    RecalcLogListResponse,
    RecalcLogResponse,
    RunSheetListItem,
    RunSheetListResponse,
    RunSheetResponse,
    SkipStopRequest,
    StopSummary,
    UnfreezeRequest,
)
from app.modules.rider_routing.service import RiderRoutingService
from app.modules.rider_routing.state import RecalcTrigger

router = APIRouter(
    prefix="/admin/rider-dispatch",
    tags=["admin-rider-dispatch"],
)

_DISPATCH = "rider.dispatch"


# ----------------------------------------------------------------------
# Live map
# ----------------------------------------------------------------------
@router.get(
    "/live-map",
    response_model=LiveMapResponse,
    summary="Latest known position of every rider with a ping in the lookback window",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def live_map(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    lookback_minutes: Annotated[int, Query(ge=1, le=180)] = 15,
) -> LiveMapResponse:
    since = utc_now() - timedelta(minutes=lookback_minutes)
    async with uow.transactional() as session:
        repo = RiderLiveLocationRepository(session)
        rows = await repo.list_active_riders_with_recent_pings(since=since)
    return LiveMapResponse(
        items=[
            LiveMapEntry(
                rider_id=r["rider_id"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                speed_kmh=r["speed_kmh"],
                heading_degrees=r["heading_degrees"],
                battery_level=r["battery_level"],
                recorded_at=r["recorded_at"],
            )
            for r in rows
        ],
        since=since,
    )


# ----------------------------------------------------------------------
# Run sheets
# ----------------------------------------------------------------------
@router.get(
    "/run-sheets",
    response_model=RunSheetListResponse,
    summary="List recent run sheets",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def list_run_sheets(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> RunSheetListResponse:
    async with uow.transactional() as session:
        repo = RunSheetRepository(session)
        rows = await repo.list_recent(limit=limit)
        return RunSheetListResponse(items=[
            RunSheetListItem(
                id=r.id,
                rider_id=r.rider_id,
                sheet_date=r.sheet_date,
                status=r.status,
                planned_stop_count=r.planned_stop_count,
                completed_stop_count=r.completed_stop_count,
                failed_stop_count=r.failed_stop_count,
            )
            for r in rows
        ])


@router.get(
    "/run-sheets/{run_sheet_id}",
    response_model=RunSheetResponse,
    summary="Full run sheet detail with all stops",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def get_run_sheet(
    run_sheet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> RunSheetResponse:
    async with uow.transactional() as session:
        repo = RunSheetRepository(session)
        stops_repo = RunSheetStopRepository(session)
        sheet = await repo.get(run_sheet_id)
        if sheet is None:
            raise RunSheetNotFoundError("Run sheet not found.")
        stops = await stops_repo.list_for_sheet(sheet.id, order="dynamic")
        from app.modules.rider_routing.api.rider import _run_sheet_to_response
        return _run_sheet_to_response(sheet, stops)


@router.post(
    "/run-sheets",
    response_model=RunSheetResponse,
    status_code=201,
    summary="Create a DRAFT run sheet bundling N delivery assignments",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def create_run_sheet(
    body: CreateRunSheetRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RunSheetResponse:
    async with uow.transactional() as session:
        svc = OpsRouteOverrideService(session)
        sheet = await svc.create_run_sheet(
            rider_id=body.rider_id,
            sheet_date=body.sheet_date,
            delivery_assignment_ids=body.delivery_assignment_ids,
            principal=principal,
            notes=body.notes,
        )
        from app.modules.rider_routing.repository import (
            RunSheetStopRepository,
        )
        stops = await RunSheetStopRepository(session).list_for_sheet(
            sheet.id, order="planned",
        )
        from app.modules.rider_routing.api.rider import _run_sheet_to_response
        return _run_sheet_to_response(sheet, stops)


@router.post(
    "/run-sheets/{run_sheet_id}/assign",
    response_model=RunSheetResponse,
    summary="Lock a DRAFT sheet (transition to ASSIGNED so the rider sees it)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def assign_run_sheet(
    run_sheet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RunSheetResponse:
    async with uow.transactional() as session:
        svc = OpsRouteOverrideService(session)
        sheet = await svc.assign_run_sheet(
            run_sheet_id=run_sheet_id, principal=principal,
        )
        stops = await RunSheetStopRepository(session).list_for_sheet(
            sheet.id, order="planned",
        )
        from app.modules.rider_routing.api.rider import _run_sheet_to_response
        return _run_sheet_to_response(sheet, stops)


@router.post(
    "/run-sheets/{run_sheet_id}/cancel",
    response_model=RunSheetResponse,
    summary="Cancel a non-terminal run sheet",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def cancel_run_sheet(
    run_sheet_id: UUID,
    body: SkipStopRequest,  # reuses {"reason": str}
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RunSheetResponse:
    async with uow.transactional() as session:
        svc = OpsRouteOverrideService(session)
        sheet = await svc.cancel_run_sheet(
            run_sheet_id=run_sheet_id,
            reason=body.reason,
            principal=principal,
        )
        stops = await RunSheetStopRepository(session).list_for_sheet(
            sheet.id, order="planned",
        )
        from app.modules.rider_routing.api.rider import _run_sheet_to_response
        return _run_sheet_to_response(sheet, stops)


@router.post(
    "/run-sheets/{run_sheet_id}/reoptimize",
    summary="Force a next-stop recompute on demand",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def reoptimize_run_sheet(
    run_sheet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
):
    async with uow.transactional() as session:
        svc = RiderRoutingService(session)
        result, changed = await svc.recommend_next_stop(
            run_sheet_id=run_sheet_id,
            principal=principal,
            trigger=RecalcTrigger.MANUAL_REFRESH,
        )
    return {
        "run_sheet_id": str(run_sheet_id),
        "changed": changed,
        "candidates_considered": result.candidates_considered,
        "chosen_stop_id": (
            str(result.chosen.stop_id) if result.chosen else None
        ),
    }


# ----------------------------------------------------------------------
# Overrides
# ----------------------------------------------------------------------
@router.post(
    "/run-sheets/{run_sheet_id}/override-next-stop",
    response_model=OverrideResponse,
    summary="Force a specific stop to be next (audit logged)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def override_next_stop(
    run_sheet_id: UUID,
    body: ForceNextStopRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OverrideResponse:
    async with uow.transactional() as session:
        svc = OpsRouteOverrideService(session)
        row = await svc.force_next_stop(
            run_sheet_id=run_sheet_id,
            target_stop_id=body.target_stop_id,
            reason=body.reason,
            principal=principal,
            expires_at=body.expires_at,
        )
    return _override_to_response(row)


@router.post(
    "/run-sheets/{run_sheet_id}/freeze",
    response_model=OverrideResponse,
    summary="Freeze a run sheet's sequence (next-stop engine returns current unchanged)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def freeze_sheet(
    run_sheet_id: UUID,
    body: FreezeRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OverrideResponse:
    async with uow.transactional() as session:
        svc = OpsRouteOverrideService(session)
        row = await svc.freeze_sequence(
            run_sheet_id=run_sheet_id,
            reason=body.reason,
            principal=principal,
            expires_at=body.expires_at,
        )
    return _override_to_response(row)


@router.post(
    "/run-sheets/{run_sheet_id}/unfreeze",
    response_model=OverrideResponse,
    summary="Unfreeze a run sheet (resume normal dynamic resequencing)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def unfreeze_sheet(
    run_sheet_id: UUID,
    body: UnfreezeRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OverrideResponse:
    async with uow.transactional() as session:
        svc = OpsRouteOverrideService(session)
        row = await svc.unfreeze_sequence(
            run_sheet_id=run_sheet_id,
            reason=body.reason,
            principal=principal,
        )
    return _override_to_response(row)


@router.post(
    "/stops/{stop_id}/skip",
    response_model=OverrideResponse,
    summary="Skip a stop (admin override, audit logged)",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def skip_stop(
    stop_id: UUID,
    body: SkipStopRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OverrideResponse:
    async with uow.transactional() as session:
        svc = OpsRouteOverrideService(session)
        row = await svc.skip_stop(
            stop_id=stop_id, reason=body.reason, principal=principal,
        )
    return _override_to_response(row)


@router.post(
    "/stops/{stop_id}/reassign",
    response_model=OverrideResponse,
    summary="Move a non-started stop into a different rider's open run sheet",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def reassign_stop(
    stop_id: UUID,
    body: ReassignStopRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> OverrideResponse:
    async with uow.transactional() as session:
        svc = OpsRouteOverrideService(session)
        row = await svc.reassign_stop(
            stop_id=stop_id,
            target_run_sheet_id=body.target_run_sheet_id,
            reason=body.reason,
            principal=principal,
        )
    return _override_to_response(row)


# ----------------------------------------------------------------------
# Audit feeds
# ----------------------------------------------------------------------
@router.get(
    "/run-sheets/{run_sheet_id}/overrides",
    response_model=OverrideListResponse,
    summary="List dispatcher overrides for a run sheet",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def list_overrides(
    run_sheet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> OverrideListResponse:
    async with uow.transactional() as session:
        repo = OpsRouteOverrideRepository(session)
        rows = await repo.list_for_sheet(run_sheet_id, limit=limit)
        return OverrideListResponse(items=[
            _override_to_response(r) for r in rows
        ])


@router.get(
    "/run-sheets/{run_sheet_id}/recalc-logs",
    response_model=RecalcLogListResponse,
    summary="Audit feed of recompute decisions for this sheet",
    dependencies=[Depends(requires_permission(_DISPATCH))],
)
async def list_recalc_logs(
    run_sheet_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> RecalcLogListResponse:
    async with uow.transactional() as session:
        repo = RouteRecalculationLogRepository(session)
        rows = await repo.list_for_sheet(run_sheet_id, limit=limit)
        return RecalcLogListResponse(items=[
            RecalcLogResponse(
                id=r.id,
                run_sheet_id=r.run_sheet_id,
                rider_id=r.rider_id,
                trigger_type=r.trigger_type,
                old_next_stop_id=r.old_next_stop_id,
                new_next_stop_id=r.new_next_stop_id,
                candidates_considered=r.candidates_considered,
                reason=r.reason,
                payload=r.payload_json,
                created_at=r.created_at,
            )
            for r in rows
        ])


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _override_to_response(row) -> OverrideResponse:
    return OverrideResponse(
        id=row.id,
        run_sheet_id=row.run_sheet_id,
        run_sheet_stop_id=row.run_sheet_stop_id,
        override_type=row.override_type,
        reason=row.reason,
        created_at=row.created_at,
        expires_at=row.expires_at,
        payload=row.payload_json,
    )
