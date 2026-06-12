"""Admin endpoints for the reporting platform.

Mounted under ``/admin/reporting``. Requires the ``reporting.admin``
permission (granted to admin + super_admin roles by default — see
``app/modules/iam/bootstrap.py`` for permission grants).

Covers:
  - definitions CRUD (list, upsert, deactivate)
  - per-(report × role) policy CRUD
  - execution log read

These endpoints let an ops admin create new reports + grant role
access *without* a code deploy — provided the report's ``code``
is already wired to a builder in ``app/modules/reporting/builders/``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.audit import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.reporting.codes import (
    ACTION_REPORT_DEFINITION_UPSERTED,
    ACTION_REPORT_POLICY_UPDATED,
)
from app.modules.reporting.errors import ReportNotFoundError
from app.modules.reporting.repository import (
    ReportAccessPolicyRepository,
    ReportDefinitionRepository,
    ReportExecutionLogRepository,
)
from app.modules.reporting.schemas import (
    DefinitionListResponse,
    DefinitionResponse,
    DefinitionUpsertRequest,
    ExecutionLogListResponse,
    ExecutionLogResponse,
    PolicyListResponse,
    PolicyResponse,
    PolicyUpsertRequest,
)

router = APIRouter(
    prefix="/admin/reporting",
    tags=["admin-reporting"],
)

_ADMIN = "reports.view"  # canonical short-form (2026-05-16); formal alias "reporting.admin" still held by every role here


# ----------------------------------------------------------------------
# Definitions
# ----------------------------------------------------------------------

@router.get(
    "/definitions",
    response_model=DefinitionListResponse,
    summary="List every registered report definition (active + inactive)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def list_definitions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> DefinitionListResponse:
    async with uow.transactional() as session:
        repo = ReportDefinitionRepository(session)
        rows = await repo.list_all()
        return DefinitionListResponse(items=[
            _def_to_response(r) for r in rows
        ])


@router.post(
    "/definitions",
    response_model=DefinitionResponse,
    summary="Create or update a report definition (upsert by code)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def upsert_definition(
    body: DefinitionUpsertRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DefinitionResponse:
    async with uow.transactional() as session:
        repo = ReportDefinitionRepository(session)
        row = await repo.upsert(
            code=body.code,
            name=body.name,
            category=body.category,
            description=body.description,
            data_source=body.data_source,
            allowed_roles=body.allowed_roles,
            allowed_export_formats=body.allowed_export_formats,
            default_filters=body.default_filters,
            columns=body.columns,
            is_active=body.is_active,
            max_rows_view=body.max_rows_view,
            max_rows_export=body.max_rows_export,
        )
        await record_audit(
            actor=principal,
            action=ACTION_REPORT_DEFINITION_UPSERTED,
            resource_type="report",
            resource_id=row.id,
            metadata={"code": row.code, "is_active": row.is_active},
        )
        return _def_to_response(row)


@router.post(
    "/definitions/{definition_id}/deactivate",
    response_model=DefinitionResponse,
    summary="Soft-disable a report (hides from list + denies new runs)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def deactivate_definition(
    definition_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DefinitionResponse:
    async with uow.transactional() as session:
        repo = ReportDefinitionRepository(session)
        row = await repo.get_by_id(definition_id)
        if row is None:
            raise ReportNotFoundError("Report not found.")
        await repo.set_active(definition_id, is_active=False)
        await session.refresh(row)
        await record_audit(
            actor=principal,
            action=ACTION_REPORT_DEFINITION_UPSERTED,
            resource_type="report",
            resource_id=row.id,
            metadata={"code": row.code, "is_active": False},
        )
        return _def_to_response(row)


# ----------------------------------------------------------------------
# Policies
# ----------------------------------------------------------------------

@router.get(
    "/definitions/{definition_id}/policies",
    response_model=PolicyListResponse,
    summary="List per-role access policies for a report",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def list_policies(
    definition_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PolicyListResponse:
    async with uow.transactional() as session:
        repo = ReportAccessPolicyRepository(session)
        rows = await repo.list_for_definition(definition_id)
        return PolicyListResponse(items=[
            PolicyResponse(
                id=p.id,
                report_definition_id=p.report_definition_id,
                role_label=p.role_label,
                scope_type=p.scope_type,
                can_view=p.can_view,
                can_export=p.can_export,
                can_schedule=p.can_schedule,
            )
            for p in rows
        ])


@router.post(
    "/definitions/{definition_id}/policies",
    response_model=PolicyResponse,
    summary="Create or update a (report × role) policy",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def upsert_policy(
    definition_id: UUID,
    body: PolicyUpsertRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PolicyResponse:
    async with uow.transactional() as session:
        defs = ReportDefinitionRepository(session)
        if await defs.get_by_id(definition_id) is None:
            raise ReportNotFoundError("Report not found.")
        repo = ReportAccessPolicyRepository(session)
        row = await repo.upsert(
            definition_id=definition_id,
            role_label=body.role_label,
            scope_type=body.scope_type,
            can_view=body.can_view,
            can_export=body.can_export,
            can_schedule=body.can_schedule,
        )
        await record_audit(
            actor=principal,
            action=ACTION_REPORT_POLICY_UPDATED,
            resource_type="report",
            resource_id=definition_id,
            metadata={
                "role_label": body.role_label,
                "scope_type": body.scope_type,
                "can_view": body.can_view,
                "can_export": body.can_export,
                "can_schedule": body.can_schedule,
            },
        )
        return PolicyResponse(
            id=row.id,
            report_definition_id=row.report_definition_id,
            role_label=row.role_label,
            scope_type=row.scope_type,
            can_view=row.can_view,
            can_export=row.can_export,
            can_schedule=row.can_schedule,
        )


@router.delete(
    "/definitions/{definition_id}/policies/{role_label}",
    status_code=204,
    response_model=None,
    summary="Remove a role's policy on a report",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def delete_policy(
    definition_id: UUID,
    role_label: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        repo = ReportAccessPolicyRepository(session)
        await repo.delete(
            definition_id=definition_id, role_label=role_label,
        )
        await record_audit(
            actor=principal,
            action=ACTION_REPORT_POLICY_UPDATED,
            resource_type="report",
            resource_id=definition_id,
            metadata={"role_label": role_label, "deleted": True},
        )


# ----------------------------------------------------------------------
# Execution log
# ----------------------------------------------------------------------

@router.get(
    "/executions",
    response_model=ExecutionLogListResponse,
    summary="Recent report execution log entries (admin audit feed)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def list_executions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ExecutionLogListResponse:
    async with uow.transactional() as session:
        repo = ReportExecutionLogRepository(session)
        rows = await repo.list_recent(limit=limit)
        return ExecutionLogListResponse(items=[
            ExecutionLogResponse(
                id=r.id,
                report_definition_id=r.report_definition_id,
                report_code=r.report_code,
                user_id=r.user_id,
                role_labels=r.role_labels_json,
                execution_type=r.execution_type,
                status=r.status,
                filters=r.filters_json,
                row_count=r.row_count,
                latency_ms=r.latency_ms,
                file_id=r.file_id,
                request_id=r.request_id,
                error_message=r.error_message,
                created_at=r.created_at,
            )
            for r in rows
        ])


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _def_to_response(row) -> DefinitionResponse:
    return DefinitionResponse(
        id=row.id,
        code=row.code,
        name=row.name,
        description=row.description,
        category=row.category,
        data_source=row.data_source,
        allowed_roles=row.allowed_roles_json,
        allowed_export_formats=row.allowed_export_formats_json,
        default_filters=row.default_filters_json,
        columns=row.columns_json,
        is_active=row.is_active,
        max_rows_view=row.max_rows_view,
        max_rows_export=row.max_rows_export,
    )
