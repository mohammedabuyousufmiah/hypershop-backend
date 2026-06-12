"""Top-level orchestration for the reporting platform.

The service ties together:
  - the registry (which builder runs)
  - the definition + policy table (whether the caller is allowed)
  - the builder (the actual SQL)
  - the exporter (file format)
  - the file/storage layer (atomic write + signed URL)
  - the execution log (audit + ops dashboards)
  - the audit log (cross-module audit trail)

Every public method emits a row in ``report_execution_logs``, so a
"who ran what when" query is one SELECT away.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import ServiceUnavailableError, ValidationError
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.reporting import permission, storage
from app.modules.reporting.codes import (
    ACTION_REPORT_DENIED,
    ACTION_REPORT_EXPORT,
    ACTION_REPORT_RUN,
)
from app.modules.reporting.errors import (
    ReportDeniedError,
    ReportExportFormatNotAllowedError,
    ReportNotFoundError,
)
from app.modules.reporting.exporters import render as render_export
from app.modules.reporting.registry import report_registry
from app.modules.reporting.repository import (
    ReportAccessPolicyRepository,
    ReportDefinitionRepository,
    ReportExecutionLogRepository,
    ReportFileRepository,
)
from app.modules.reporting.state import (
    ExecutionStatus,
    ExecutionType,
    ExportFormat,
)

_log = get_logger("hypershop.reporting.service")


async def _log_failure_in_new_uow(
    *,
    definition_id: UUID | None,
    report_code: str,
    user_id: UUID | None,
    role_labels: list[str],
    execution_type: str,
    status: str,
    filters: dict[str, Any],
    request_id: str,
    error_message: str,
    audit_action: str | None = None,
    audit_resource_id: UUID | None = None,
    audit_metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a failure / denied execution log row in a NEW UoW.

    Why this exists: the parent caller's UoW is about to roll back
    (because the calling endpoint will raise). If we wrote the failure
    row to the parent UoW it would also roll back — losing exactly the
    audit trail that ops needs to debug "why did my report fail?".

    Opens a fresh ``UnitOfWork().transactional()`` (which gets its own
    session from the sessionmaker, not a SAVEPOINT on the parent) and
    commits independently. Best-effort: if the failure-log write itself
    fails, we swallow + log so the original exception still propagates.
    """
    from app.core.audit import record_audit
    from app.core.db.session import get_sessionmaker
    from app.core.db.uow import _current_session
    from app.modules.reporting.repository import (
        ReportExecutionLogRepository,
    )

    # Open a session that's INDEPENDENT of any parent UoW. We can't use
    # ``UnitOfWork().transactional()`` because it auto-detects the
    # parent via the contextvar and creates a SAVEPOINT — which rolls
    # back with the parent. Bypass: open from the sessionmaker directly
    # + temporarily swap the contextvar so audit's current_session()
    # picks up our fresh session, then restore.
    sessionmaker = get_sessionmaker()
    parent_token = _current_session.set(None)
    try:
        async with sessionmaker() as session:
            inner_token = _current_session.set(session)
            try:
                async with session.begin():
                    log_repo = ReportExecutionLogRepository(session)
                    await log_repo.add(
                        report_definition_id=definition_id,
                        report_code=report_code,
                        user_id=user_id,
                        role_labels=role_labels,
                        execution_type=execution_type,
                        status=status,
                        filters=filters,
                        row_count=0,
                        latency_ms=0,
                        request_id=request_id,
                        error_message=error_message[:1024],
                    )
                    if audit_action is not None:
                        actor = _FakePrincipal(
                            user_id=user_id,
                            roles=frozenset(role_labels or ()),
                        ) if user_id is not None else None
                        await record_audit(
                            actor=actor,
                            action=audit_action,
                            resource_type="report",
                            resource_id=audit_resource_id,
                            outcome="failure",
                            metadata=audit_metadata or {},
                        )
            finally:
                _current_session.reset(inner_token)
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.exception(
            "report_failure_audit_write_failed",
            error=str(e),
            report_code=report_code,
        )
    finally:
        _current_session.reset(parent_token)


# Synthetic principal used when we don't have the real one (failure
# path called from inside a try/except where we may have lost it).
# record_audit only reads user_id + has_permission + has_role, so a
# minimal stand-in is enough.
from dataclasses import dataclass, field as _dc_field


@dataclass
class _FakePrincipal:
    user_id: UUID | None
    roles: frozenset = _dc_field(default_factory=frozenset)
    permissions: frozenset = _dc_field(default_factory=frozenset)

    def has_permission(self, perm: str) -> bool:
        return False

    def has_role(self, role: str) -> bool:
        return role in self.roles


class ReportingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.definitions = ReportDefinitionRepository(session)
        self.policies = ReportAccessPolicyRepository(session)
        self.executions = ReportExecutionLogRepository(session)
        self.files = ReportFileRepository(session)

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    async def list_reports_for(
        self, principal: Principal | SystemPrincipal,
    ) -> list[dict[str, Any]]:
        """Return all reports the principal has any access to."""
        defs = await self.definitions.list_active()
        out: list[dict[str, Any]] = []
        for d in defs:
            policies = await self.policies.list_for_definition(d.id)
            decision = permission.evaluate(
                principal=principal, definition=d, policies=policies,
            )
            if decision.allowed:
                out.append({
                    "id": d.id,
                    "code": d.code,
                    "name": d.name,
                    "description": d.description,
                    "category": d.category,
                    "columns": d.columns_json,
                    "default_filters": d.default_filters_json,
                    "allowed_export_formats": d.allowed_export_formats_json,
                    "max_rows_view": d.max_rows_view,
                    "max_rows_export": d.max_rows_export,
                    "can_view": decision.can_view,
                    "can_export": decision.can_export,
                    "can_schedule": decision.can_schedule,
                    "scope_type": decision.scope_type,
                })
        return out

    # ------------------------------------------------------------------
    # Run for view (JSON in-line)
    # ------------------------------------------------------------------

    async def run_for_view(
        self, *,
        code: str,
        principal: Principal | SystemPrincipal,
        filters: dict[str, Any] | None,
        request_id: str = "",
    ) -> dict[str, Any]:
        definition, decision = await self._authorise(
            code=code, principal=principal, action="view",
        )
        builder_entry = report_registry.get(code)
        if builder_entry is None:
            raise ServiceUnavailableError(
                f"Report '{code}' has no registered builder.",
            )

        merged = permission.merge_filters(
            request_filters=filters, definition=definition,
        )
        max_rows = definition.max_rows_view

        started = time.perf_counter()
        try:
            rows = await builder_entry.builder(
                session=self.session,
                filters=merged,
                scope_type=decision.scope_type,
                current_user_id=getattr(principal, "user_id", None),
                max_rows=max_rows,
            )
        except Exception as e:  # noqa: BLE001 — we WANT the audit row
            # Write the failure log in an INDEPENDENT UoW because the
            # caller's transaction is about to roll back when we
            # re-raise — that would lose this row otherwise.
            await _log_failure_in_new_uow(
                definition_id=definition.id,
                report_code=definition.code,
                user_id=getattr(principal, "user_id", None) if isinstance(
                    principal, Principal,
                ) else None,
                role_labels=sorted(getattr(principal, "roles", [])),
                execution_type=ExecutionType.VIEW.value,
                status=ExecutionStatus.FAILED.value,
                filters=merged,
                request_id=request_id,
                error_message=str(e),
                audit_action=ACTION_REPORT_RUN,
                audit_resource_id=definition.id,
                audit_metadata={"code": code, "error": str(e)[:500]},
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        # Row cap is a defence-in-depth — builders honour max_rows,
        # but a buggy builder could still return more.
        rows = rows[:max_rows]
        await self._log_execution(
            definition=definition,
            principal=principal,
            execution_type=ExecutionType.VIEW.value,
            status=ExecutionStatus.SUCCESS.value,
            filters=merged,
            row_count=len(rows),
            latency_ms=latency_ms,
            request_id=request_id,
        )
        await record_audit(
            actor=principal,
            action=ACTION_REPORT_RUN,
            resource_type="report",
            resource_id=definition.id,
            metadata={
                "code": code,
                "row_count": len(rows),
                "latency_ms": latency_ms,
            },
        )
        return {
            "code": code,
            "name": definition.name,
            "columns": definition.columns_json,
            "rows": rows,
            "row_count": len(rows),
            "scope_type": decision.scope_type,
            "filters_applied": merged,
            "latency_ms": latency_ms,
        }

    # ------------------------------------------------------------------
    # Run for export (file output + signed URL)
    # ------------------------------------------------------------------

    async def run_for_export(
        self, *,
        code: str,
        principal: Principal | SystemPrincipal,
        filters: dict[str, Any] | None,
        export_format: str,
        request_id: str = "",
        execution_type: str = ExecutionType.EXPORT.value,
    ) -> dict[str, Any]:
        if export_format == ExportFormat.JSON.value:
            raise ValidationError(
                "JSON export uses /run; choose csv, xlsx, or pdf.",
            )
        try:
            ExportFormat(export_format)
        except ValueError as e:
            raise ValidationError(
                f"Unknown export format: {export_format}",
            ) from e

        definition, decision = await self._authorise(
            code=code, principal=principal,
            action="export" if execution_type != ExecutionType.SCHEDULED.value
            else "schedule",
        )
        if export_format not in (definition.allowed_export_formats_json or []):
            raise ReportExportFormatNotAllowedError(
                f"Export format '{export_format}' is not enabled for "
                f"this report.",
            )

        builder_entry = report_registry.get(code)
        if builder_entry is None:
            raise ServiceUnavailableError(
                f"Report '{code}' has no registered builder.",
            )

        merged = permission.merge_filters(
            request_filters=filters, definition=definition,
        )
        max_rows = definition.max_rows_export

        started = time.perf_counter()
        try:
            rows = await builder_entry.builder(
                session=self.session,
                filters=merged,
                scope_type=decision.scope_type,
                current_user_id=getattr(principal, "user_id", None),
                max_rows=max_rows,
            )
        except Exception as e:  # noqa: BLE001
            await _log_failure_in_new_uow(
                definition_id=definition.id,
                report_code=definition.code,
                user_id=getattr(principal, "user_id", None) if isinstance(
                    principal, Principal,
                ) else None,
                role_labels=sorted(getattr(principal, "roles", [])),
                execution_type=execution_type,
                status=ExecutionStatus.FAILED.value,
                filters=merged,
                request_id=request_id,
                error_message=str(e),
                audit_action=ACTION_REPORT_EXPORT,
                audit_resource_id=definition.id,
                audit_metadata={
                    "code": code, "format": export_format,
                    "error": str(e)[:500],
                },
            )
            raise

        rows = rows[:max_rows]
        generated_at = utc_now()
        payload = render_export(
            fmt=export_format,
            title=definition.name,
            columns=definition.columns_json or [],
            rows=rows,
            generated_at=generated_at,
        )
        # Reserve a path + write atomically. file_id comes from the
        # filename so the ORM row points to the same UUID.
        file_id, path = storage.reserve_path(fmt=export_format)
        sha = storage.write_atomically(path, payload)
        expires_at = storage.default_expiry()
        owner_id = getattr(principal, "user_id", None) if isinstance(
            principal, Principal,
        ) else None

        # Record the file row first so the execution log can FK to it.
        # We override the auto-generated UUID with the one from
        # reserve_path so the on-disk filename matches the ORM PK.
        file_row = await self.files.add(
            id=file_id,
            report_code=code,
            user_id=owner_id,
            format=export_format,
            storage_path=str(path),
            size_bytes=len(payload),
            sha256=sha,
            expires_at=expires_at,
            row_count=len(rows),
        )

        latency_ms = int((time.perf_counter() - started) * 1000)
        log_row = await self._log_execution(
            definition=definition,
            principal=principal,
            execution_type=execution_type,
            status=ExecutionStatus.SUCCESS.value,
            filters=merged,
            row_count=len(rows),
            latency_ms=latency_ms,
            file_id=file_row.id,
            request_id=request_id,
        )
        await record_audit(
            actor=principal,
            action=ACTION_REPORT_EXPORT,
            resource_type="report",
            resource_id=definition.id,
            metadata={
                "code": code,
                "format": export_format,
                "file_id": str(file_row.id),
                "row_count": len(rows),
                "latency_ms": latency_ms,
                "execution_log_id": str(log_row.id),
            },
        )

        token = storage.make_signed_token(
            file_id=file_row.id,
            user_id=owner_id,
            expires_at=expires_at,
        )
        return {
            "code": code,
            "name": definition.name,
            "format": export_format,
            "file_id": file_row.id,
            "size_bytes": file_row.size_bytes,
            "row_count": len(rows),
            "expires_at": expires_at,
            "download_token": token,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _authorise(
        self, *,
        code: str,
        principal: Principal | SystemPrincipal,
        action: str,
    ):
        definition = await self.definitions.get_by_code(code)
        if definition is None or not definition.is_active:
            raise ReportNotFoundError(f"Report '{code}' not found.")
        policies = await self.policies.list_for_definition(definition.id)
        decision = permission.evaluate(
            principal=principal, definition=definition, policies=policies,
        )
        denied_reason = decision.reason
        permitted = (
            (action == "view" and decision.can_view)
            or (action == "export" and decision.can_export)
            or (action == "schedule" and decision.can_schedule)
        )
        if not (decision.allowed and permitted):
            # Denial → caller will raise ReportDeniedError → UoW rolls
            # back. Persist the denied execution log + audit row in a
            # NEW UoW so ops can spot abuse patterns.
            await _log_failure_in_new_uow(
                definition_id=definition.id,
                report_code=definition.code,
                user_id=getattr(principal, "user_id", None) if isinstance(
                    principal, Principal,
                ) else None,
                role_labels=sorted(getattr(principal, "roles", [])),
                execution_type=(
                    ExecutionType.EXPORT.value
                    if action == "export"
                    else ExecutionType.VIEW.value
                ),
                status=ExecutionStatus.DENIED.value,
                filters={},
                request_id="",
                error_message=denied_reason or f"action={action}",
                audit_action=ACTION_REPORT_DENIED,
                audit_resource_id=definition.id,
                audit_metadata={"code": code, "action": action},
            )
            # The in-UoW audit below WOULD also rollback, so it's
            # redundant given the new UoW already wrote the audit. We
            # still call it here for the rare case where the parent
            # commits after a denial (shouldn't happen with the
            # immediate raise below — but defensive).
            await record_audit(
                actor=principal,
                action=ACTION_REPORT_DENIED,
                resource_type="report",
                resource_id=definition.id,
                metadata={"code": code, "action": action},
            )
            raise ReportDeniedError(
                denied_reason or
                f"You don't have '{action}' permission for this report.",
            )
        return definition, decision

    async def _log_execution(
        self, *,
        definition,
        principal: Principal | SystemPrincipal,
        execution_type: str,
        status: str,
        filters: dict[str, Any],
        row_count: int,
        latency_ms: int,
        file_id: UUID | None = None,
        request_id: str = "",
        error_message: str = "",
    ):
        return await self.executions.add(
            report_definition_id=definition.id,
            report_code=definition.code,
            user_id=getattr(principal, "user_id", None) if isinstance(
                principal, Principal,
            ) else None,
            role_labels=sorted(getattr(principal, "roles", [])),
            execution_type=execution_type,
            status=status,
            filters=filters,
            row_count=row_count,
            latency_ms=latency_ms,
            file_id=file_id,
            request_id=request_id,
            error_message=error_message,
        )


# ------------------------------------------------------------------
# Schedule helper — pure function (no DB) for next-run computation
# ------------------------------------------------------------------

def compute_next_run(
    *,
    frequency: str,
    run_hour_local: int,
    run_day_of_week: int | None,
    run_day_of_month: int | None,
    timezone_offset_hours: int,
    after: datetime,
) -> datetime:
    """Compute the next run timestamp (UTC) AFTER ``after``.

    Local-wall-clock semantics: the user picks an hour in their TZ;
    we add the offset to map to UTC. We always advance past the
    ``after`` value, so calling this with ``after=now`` after a run
    immediately schedules the next future occurrence.
    """
    from datetime import timedelta, timezone

    # Convert ``after`` (UTC) to a local datetime for arithmetic.
    local_after = after.astimezone(
        timezone(timedelta(hours=timezone_offset_hours)),
    )

    if frequency == "daily":
        candidate = local_after.replace(
            hour=run_hour_local, minute=0, second=0, microsecond=0,
        )
        if candidate <= local_after:
            candidate += timedelta(days=1)
    elif frequency == "weekly":
        if run_day_of_week is None:
            run_day_of_week = 0
        candidate = local_after.replace(
            hour=run_hour_local, minute=0, second=0, microsecond=0,
        )
        days_ahead = (run_day_of_week - candidate.weekday()) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= local_after:
            candidate += timedelta(days=7)
    elif frequency == "monthly":
        dom = run_day_of_month or 1
        # Move forward month-by-month until we pass ``after``.
        year, month = local_after.year, local_after.month
        while True:
            try:
                candidate = local_after.replace(
                    year=year, month=month, day=min(dom, 28),
                    hour=run_hour_local, minute=0, second=0, microsecond=0,
                )
            except ValueError:
                # Should not happen with day clamped to 28.
                candidate = local_after
            if candidate > local_after:
                break
            month += 1
            if month > 12:
                month = 1
                year += 1
    else:
        raise ValidationError(f"Unknown schedule frequency: {frequency}")

    return candidate.astimezone(timezone.utc)
