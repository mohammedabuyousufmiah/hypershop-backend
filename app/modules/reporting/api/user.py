"""User-facing reporting endpoints.

Mounted at ``/reports``. Every authenticated user can hit these —
the service layer enforces per-(report × role) access via the
``report_access_policies`` table. Users with no allowed reports just
see an empty list from ``GET /reports``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ForbiddenError, ValidationError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.reporting.codes import ACTION_REPORT_FILE_DOWNLOADED
from app.core.audit import record_audit
from app.modules.reporting.errors import (
    ReportFileExpiredError,
    ReportFileNotFoundError,
    ReportSavedFilterNotFoundError,
    ReportScheduleNotFoundError,
)
from app.modules.reporting.repository import (
    ReportDefinitionRepository,
    ReportFileRepository,
    ReportSavedFilterRepository,
    ReportScheduleRepository,
)
from app.modules.reporting.schemas import (
    ExecutionLogListResponse,
    ExecutionLogResponse,
    ExportReportRequest,
    ExportReportResponse,
    ReportListItem,
    ReportListResponse,
    RunReportRequest,
    RunReportResponse,
    SavedFilterCreateRequest,
    SavedFilterListResponse,
    SavedFilterResponse,
    SavedFilterUpdateRequest,
    ScheduleCreateRequest,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleUpdateRequest,
)
from app.modules.reporting.service import ReportingService, compute_next_run
from app.modules.reporting.storage import verify_signed_token

router = APIRouter(prefix="/reports", tags=["reports"])


# ----------------------------------------------------------------------
# List + run + export
# ----------------------------------------------------------------------

@router.get(
    "",
    response_model=ReportListResponse,
    summary="List reports the caller can access",
)
async def list_reports(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReportListResponse:
    async with uow.transactional() as session:
        svc = ReportingService(session)
        items = await svc.list_reports_for(principal)
    return ReportListResponse(
        items=[ReportListItem(**i) for i in items],
    )


@router.post(
    "/{code}/run",
    response_model=RunReportResponse,
    summary="Execute a report and return JSON rows",
)
async def run_report(
    code: str,
    body: RunReportRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    x_request_id: Annotated[str | None, Header()] = None,
) -> RunReportResponse:
    async with uow.transactional() as session:
        svc = ReportingService(session)
        result = await svc.run_for_view(
            code=code,
            principal=principal,
            filters=body.filters,
            request_id=x_request_id or "",
        )
    return RunReportResponse(**result)


@router.post(
    "/{code}/export",
    response_model=ExportReportResponse,
    summary="Generate a downloadable file (csv / xlsx / pdf) and return a signed token",
)
async def export_report(
    code: str,
    body: ExportReportRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    x_request_id: Annotated[str | None, Header()] = None,
) -> ExportReportResponse:
    async with uow.transactional() as session:
        svc = ReportingService(session)
        result = await svc.run_for_export(
            code=code,
            principal=principal,
            filters=body.filters,
            export_format=body.export_format,
            request_id=x_request_id or "",
        )
    return ExportReportResponse(**result)


# ----------------------------------------------------------------------
# File download (signed)
# ----------------------------------------------------------------------

@router.get(
    "/files/{file_id}/download",
    summary="Download a generated report file using its signed token",
    response_class=FileResponse,
)
async def download_file(
    file_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    token: Annotated[str, Query(min_length=10, max_length=512)],
) -> FileResponse:
    # Token verification first — fast and stateless.
    verify_signed_token(
        token=token,
        expected_file_id=file_id,
        caller_user_id=principal.user_id,
    )
    async with uow.transactional() as session:
        repo = ReportFileRepository(session)
        row = await repo.get(file_id)
        if row is None:
            raise ReportFileNotFoundError("Report file not found.")
        if row.expires_at <= utc_now():
            raise ReportFileExpiredError("Report file has expired.")
        path = Path(row.storage_path)
        if not path.is_file():
            # The DB row exists but the file isn't there anymore —
            # treat as expired so the caller gets 410.
            raise ReportFileExpiredError("Report file is no longer available.")

        # Audit the download. Don't fail the download if audit raises —
        # it shouldn't, but if it does the file is more important.
        try:
            await record_audit(
                actor=principal,
                action=ACTION_REPORT_FILE_DOWNLOADED,
                resource_type="report_file",
                resource_id=row.id,
                metadata={
                    "report_code": row.report_code,
                    "format": row.format,
                    "size_bytes": row.size_bytes,
                },
            )
        except Exception:  # noqa: BLE001
            pass

    media_type = {
        "csv": "text/csv; charset=utf-8",
        "xlsx": (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        "pdf": "application/pdf",
    }.get(row.format, "application/octet-stream")
    filename = f"{row.report_code}.{row.format}"
    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=filename,
    )


# ----------------------------------------------------------------------
# Saved filters CRUD
# ----------------------------------------------------------------------

@router.get(
    "/{code}/saved-filters",
    response_model=SavedFilterListResponse,
    summary="List saved filters for the current user on this report",
)
async def list_saved_filters(
    code: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SavedFilterListResponse:
    async with uow.transactional() as session:
        defs = ReportDefinitionRepository(session)
        d = await defs.get_by_code(code)
        if d is None:
            return SavedFilterListResponse(items=[])
        repo = ReportSavedFilterRepository(session)
        rows = await repo.list_for_user_report(
            user_id=principal.user_id, definition_id=d.id,
        )
        return SavedFilterListResponse(items=[
            SavedFilterResponse(
                id=r.id,
                report_definition_id=r.report_definition_id,
                user_id=r.user_id,
                name=r.name,
                filters=r.filters_json,
                is_default=r.is_default,
            )
            for r in rows
        ])


@router.post(
    "/{code}/saved-filters",
    response_model=SavedFilterResponse,
    status_code=201,
    summary="Create a saved filter for this report",
)
async def create_saved_filter(
    code: str,
    body: SavedFilterCreateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SavedFilterResponse:
    async with uow.transactional() as session:
        defs = ReportDefinitionRepository(session)
        d = await defs.get_by_code(code)
        if d is None:
            raise ValidationError(f"Unknown report code: {code}")
        repo = ReportSavedFilterRepository(session)
        if body.is_default:
            await repo.clear_default(
                user_id=principal.user_id, definition_id=d.id,
            )
        row = await repo.add(
            report_definition_id=d.id,
            user_id=principal.user_id,
            name=body.name,
            filters_json=body.filters,
            is_default=body.is_default,
        )
        return SavedFilterResponse(
            id=row.id,
            report_definition_id=row.report_definition_id,
            user_id=row.user_id,
            name=row.name,
            filters=row.filters_json,
            is_default=row.is_default,
        )


@router.patch(
    "/saved-filters/{filter_id}",
    response_model=SavedFilterResponse,
    summary="Update a saved filter (only the owner may edit)",
)
async def update_saved_filter(
    filter_id: UUID,
    body: SavedFilterUpdateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SavedFilterResponse:
    async with uow.transactional() as session:
        repo = ReportSavedFilterRepository(session)
        row = await repo.get(filter_id)
        if row is None:
            raise ReportSavedFilterNotFoundError("Saved filter not found.")
        if row.user_id != principal.user_id:
            raise ForbiddenError("You can only edit your own saved filters.")
        if body.is_default:
            await repo.clear_default(
                user_id=row.user_id, definition_id=row.report_definition_id,
            )
        await repo.update_filters(
            filter_id=filter_id,
            filters=body.filters,
            is_default=body.is_default,
        )
        # Re-read to return canonical state.
        await session.refresh(row)
        return SavedFilterResponse(
            id=row.id,
            report_definition_id=row.report_definition_id,
            user_id=row.user_id,
            name=row.name,
            filters=row.filters_json,
            is_default=row.is_default,
        )


@router.delete(
    "/saved-filters/{filter_id}",
    status_code=204,
    response_model=None,
    summary="Delete a saved filter (only the owner may delete)",
)
async def delete_saved_filter(
    filter_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        repo = ReportSavedFilterRepository(session)
        row = await repo.get(filter_id)
        if row is None:
            raise ReportSavedFilterNotFoundError("Saved filter not found.")
        if row.user_id != principal.user_id:
            raise ForbiddenError("You can only delete your own saved filters.")
        await repo.delete(filter_id)


# ----------------------------------------------------------------------
# Schedules CRUD
# ----------------------------------------------------------------------

@router.get(
    "/schedules",
    response_model=ScheduleListResponse,
    summary="List the current user's schedules",
)
async def list_schedules(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ScheduleListResponse:
    async with uow.transactional() as session:
        repo = ReportScheduleRepository(session)
        defs = ReportDefinitionRepository(session)
        rows = await repo.list_for_user(principal.user_id)
        items: list[ScheduleResponse] = []
        for s in rows:
            d = await defs.get_by_id(s.report_definition_id)
            items.append(ScheduleResponse(
                id=s.id,
                report_definition_id=s.report_definition_id,
                report_code=d.code if d else "",
                user_id=s.user_id,
                frequency=s.frequency,
                run_hour_local=s.run_hour_local,
                run_day_of_week=s.run_day_of_week,
                run_day_of_month=s.run_day_of_month,
                timezone_offset_hours=s.timezone_offset_hours,
                export_format=s.export_format,
                filters=s.filters_json,
                recipient_emails=s.recipient_emails_json,
                is_active=s.is_active,
                next_run_at=s.next_run_at,
                last_run_at=s.last_run_at,
            ))
        return ScheduleListResponse(items=items)


@router.post(
    "/schedules",
    response_model=ScheduleResponse,
    status_code=201,
    summary="Create a recurring report schedule",
)
async def create_schedule(
    body: ScheduleCreateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ScheduleResponse:
    async with uow.transactional() as session:
        defs = ReportDefinitionRepository(session)
        d = await defs.get_by_code(body.report_code)
        if d is None:
            raise ValidationError(f"Unknown report code: {body.report_code}")

        # Authorise — must have can_schedule on this report.
        svc = ReportingService(session)
        await svc._authorise(  # noqa: SLF001 — internal helper
            code=body.report_code, principal=principal, action="schedule",
        )
        if body.export_format not in (d.allowed_export_formats_json or []):
            raise ValidationError(
                f"Format '{body.export_format}' is not enabled for this report.",
            )

        next_at = compute_next_run(
            frequency=body.frequency,
            run_hour_local=body.run_hour_local,
            run_day_of_week=body.run_day_of_week,
            run_day_of_month=body.run_day_of_month,
            timezone_offset_hours=body.timezone_offset_hours,
            after=utc_now(),
        )
        repo = ReportScheduleRepository(session)
        row = await repo.add(
            report_definition_id=d.id,
            user_id=principal.user_id,
            frequency=body.frequency,
            run_hour_local=body.run_hour_local,
            run_day_of_week=body.run_day_of_week,
            run_day_of_month=body.run_day_of_month,
            timezone_offset_hours=body.timezone_offset_hours,
            export_format=body.export_format,
            filters_json=body.filters,
            recipient_emails_json=body.recipient_emails,
            is_active=True,
            next_run_at=next_at,
        )
        return _schedule_to_response(row, body.report_code)


@router.patch(
    "/schedules/{schedule_id}",
    response_model=ScheduleResponse,
    summary="Update a schedule (only the owner may edit)",
)
async def update_schedule(
    schedule_id: UUID,
    body: ScheduleUpdateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ScheduleResponse:
    async with uow.transactional() as session:
        repo = ReportScheduleRepository(session)
        row = await repo.get(schedule_id)
        if row is None:
            raise ReportScheduleNotFoundError("Schedule not found.")
        if row.user_id != principal.user_id:
            raise ForbiddenError("You can only edit your own schedules.")

        # Apply incoming patches in-place (kept simple — no separate
        # repo method since these are per-row mutations).
        for field in (
            "frequency", "run_hour_local", "run_day_of_week",
            "run_day_of_month", "timezone_offset_hours", "export_format",
            "is_active",
        ):
            v = getattr(body, field)
            if v is not None:
                setattr(row, field, v)
        if body.filters is not None:
            row.filters_json = body.filters
        if body.recipient_emails is not None:
            row.recipient_emails_json = body.recipient_emails

        # Recompute next_run_at if any timing field changed.
        timing_changed = any(
            getattr(body, f) is not None
            for f in (
                "frequency", "run_hour_local", "run_day_of_week",
                "run_day_of_month", "timezone_offset_hours",
            )
        )
        if timing_changed and row.is_active:
            row.next_run_at = compute_next_run(
                frequency=row.frequency,
                run_hour_local=row.run_hour_local,
                run_day_of_week=row.run_day_of_week,
                run_day_of_month=row.run_day_of_month,
                timezone_offset_hours=row.timezone_offset_hours,
                after=utc_now(),
            )
        if body.is_active is False:
            row.next_run_at = None
        await session.flush()

        defs = ReportDefinitionRepository(session)
        d = await defs.get_by_id(row.report_definition_id)
        return _schedule_to_response(row, d.code if d else "")


@router.delete(
    "/schedules/{schedule_id}",
    status_code=204,
    response_model=None,
    summary="Delete a schedule (only the owner may delete)",
)
async def delete_schedule(
    schedule_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        repo = ReportScheduleRepository(session)
        row = await repo.get(schedule_id)
        if row is None:
            raise ReportScheduleNotFoundError("Schedule not found.")
        if row.user_id != principal.user_id:
            raise ForbiddenError("You can only delete your own schedules.")
        await repo.delete(schedule_id)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _schedule_to_response(row, report_code: str) -> ScheduleResponse:
    return ScheduleResponse(
        id=row.id,
        report_definition_id=row.report_definition_id,
        report_code=report_code,
        user_id=row.user_id,
        frequency=row.frequency,
        run_hour_local=row.run_hour_local,
        run_day_of_week=row.run_day_of_week,
        run_day_of_month=row.run_day_of_month,
        timezone_offset_hours=row.timezone_offset_hours,
        export_format=row.export_format,
        filters=row.filters_json,
        recipient_emails=row.recipient_emails_json,
        is_active=row.is_active,
        next_run_at=row.next_run_at,
        last_run_at=row.last_run_at,
    )
