"""Persistence wrappers for the 6 reporting tables.

Thin SQLAlchemy core — every method is a single statement (or a tiny
sequence of them). Side-effect-free queries don't flush; mutations
flush (so the caller sees the new row inside the same transaction).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reporting.models import (
    ReportAccessPolicy,
    ReportDefinition,
    ReportExecutionLog,
    ReportFile,
    ReportSavedFilter,
    ReportSchedule,
)


# ============================================================
#  ReportDefinitionRepository
# ============================================================
class ReportDefinitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(self, code: str) -> ReportDefinition | None:
        return (
            await self.session.execute(
                select(ReportDefinition).where(ReportDefinition.code == code),
            )
        ).scalar_one_or_none()

    async def get_by_id(self, definition_id: UUID) -> ReportDefinition | None:
        return await self.session.get(ReportDefinition, definition_id)

    async def list_active(self) -> Sequence[ReportDefinition]:
        return (
            (
                await self.session.execute(
                    select(ReportDefinition)
                    .where(ReportDefinition.is_active.is_(True))
                    .order_by(ReportDefinition.category, ReportDefinition.code),
                )
            )
            .scalars()
            .all()
        )

    async def list_all(self) -> Sequence[ReportDefinition]:
        return (
            (
                await self.session.execute(
                    select(ReportDefinition)
                    .order_by(ReportDefinition.category, ReportDefinition.code),
                )
            )
            .scalars()
            .all()
        )

    async def upsert(
        self, *,
        code: str,
        name: str,
        category: str,
        description: str = "",
        data_source: str = "",
        allowed_roles: list[str],
        allowed_export_formats: list[str],
        default_filters: dict[str, Any] | None = None,
        columns: list[dict[str, str]],
        is_active: bool = True,
        max_rows_view: int = 500,
        max_rows_export: int = 100000,
    ) -> ReportDefinition:
        values = {
            "code": code,
            "name": name,
            "description": description,
            "category": category,
            "data_source": data_source,
            "allowed_roles_json": list(allowed_roles),
            "allowed_export_formats_json": list(allowed_export_formats),
            "default_filters_json": default_filters or {},
            "columns_json": list(columns),
            "is_active": is_active,
            "max_rows_view": max_rows_view,
            "max_rows_export": max_rows_export,
        }
        stmt = (
            pg_insert(ReportDefinition.__table__)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["code"],
                set_={k: v for k, v in values.items() if k != "code"},
            )
            .returning(ReportDefinition.__table__.c.id)
        )
        new_id = (await self.session.execute(stmt)).scalar_one()
        # Reload via ORM so the caller has a managed instance.
        await self.session.flush()
        return await self.session.get(ReportDefinition, new_id)  # type: ignore[return-value]

    async def set_active(self, definition_id: UUID, *, is_active: bool) -> int:
        result = await self.session.execute(
            update(ReportDefinition)
            .where(ReportDefinition.id == definition_id)
            .values(is_active=is_active),
        )
        return int(result.rowcount or 0)


# ============================================================
#  ReportAccessPolicyRepository
# ============================================================
class ReportAccessPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_definition(
        self, definition_id: UUID,
    ) -> Sequence[ReportAccessPolicy]:
        return (
            (
                await self.session.execute(
                    select(ReportAccessPolicy)
                    .where(
                        ReportAccessPolicy.report_definition_id == definition_id,
                    )
                    .order_by(ReportAccessPolicy.role_label),
                )
            )
            .scalars()
            .all()
        )

    async def list_for_role(
        self, role_label: str,
    ) -> Sequence[ReportAccessPolicy]:
        return (
            (
                await self.session.execute(
                    select(ReportAccessPolicy)
                    .where(ReportAccessPolicy.role_label == role_label),
                )
            )
            .scalars()
            .all()
        )

    async def get(
        self, *, definition_id: UUID, role_label: str,
    ) -> ReportAccessPolicy | None:
        return (
            await self.session.execute(
                select(ReportAccessPolicy)
                .where(
                    and_(
                        ReportAccessPolicy.report_definition_id == definition_id,
                        ReportAccessPolicy.role_label == role_label,
                    ),
                ),
            )
        ).scalar_one_or_none()

    async def upsert(
        self, *,
        definition_id: UUID,
        role_label: str,
        scope_type: str,
        can_view: bool,
        can_export: bool,
        can_schedule: bool,
    ) -> ReportAccessPolicy:
        values = {
            "report_definition_id": definition_id,
            "role_label": role_label,
            "scope_type": scope_type,
            "can_view": can_view,
            "can_export": can_export,
            "can_schedule": can_schedule,
        }
        stmt = (
            pg_insert(ReportAccessPolicy.__table__)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["report_definition_id", "role_label"],
                set_={
                    "scope_type": scope_type,
                    "can_view": can_view,
                    "can_export": can_export,
                    "can_schedule": can_schedule,
                },
            )
            .returning(ReportAccessPolicy.__table__.c.id)
        )
        new_id = (await self.session.execute(stmt)).scalar_one()
        await self.session.flush()
        return await self.session.get(  # type: ignore[return-value]
            ReportAccessPolicy, new_id,
        )

    async def delete(
        self, *, definition_id: UUID, role_label: str,
    ) -> int:
        result = await self.session.execute(
            delete(ReportAccessPolicy)
            .where(
                and_(
                    ReportAccessPolicy.report_definition_id == definition_id,
                    ReportAccessPolicy.role_label == role_label,
                ),
            ),
        )
        return int(result.rowcount or 0)


# ============================================================
#  ReportExecutionLogRepository
# ============================================================
class ReportExecutionLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self, *,
        report_definition_id: UUID | None,
        report_code: str,
        user_id: UUID | None,
        role_labels: list[str],
        execution_type: str,
        status: str,
        filters: dict[str, Any],
        row_count: int,
        latency_ms: int,
        file_id: UUID | None = None,
        request_id: str = "",
        error_message: str = "",
    ) -> ReportExecutionLog:
        row = ReportExecutionLog(
            report_definition_id=report_definition_id,
            report_code=report_code,
            user_id=user_id,
            role_labels_json=list(role_labels),
            execution_type=execution_type,
            status=status,
            filters_json=filters or {},
            row_count=row_count,
            latency_ms=latency_ms,
            file_id=file_id,
            request_id=(request_id or "")[:64],
            error_message=(error_message or "")[:1024],
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, log_id: UUID) -> ReportExecutionLog | None:
        return await self.session.get(ReportExecutionLog, log_id)

    async def list_recent(
        self, *, limit: int = 100,
    ) -> Sequence[ReportExecutionLog]:
        return (
            (
                await self.session.execute(
                    select(ReportExecutionLog)
                    .order_by(ReportExecutionLog.created_at.desc())
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )


# ============================================================
#  ReportScheduleRepository
# ============================================================
class ReportScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, schedule_id: UUID) -> ReportSchedule | None:
        return await self.session.get(ReportSchedule, schedule_id)

    async def list_for_user(self, user_id: UUID) -> Sequence[ReportSchedule]:
        return (
            (
                await self.session.execute(
                    select(ReportSchedule)
                    .where(ReportSchedule.user_id == user_id)
                    .order_by(ReportSchedule.created_at.desc()),
                )
            )
            .scalars()
            .all()
        )

    async def list_due(
        self, *, now_utc: datetime, limit: int = 100,
    ) -> Sequence[ReportSchedule]:
        """Schedules whose ``next_run_at`` has elapsed and that are active.

        Ordered by oldest-due-first so a backlog drains FIFO.
        """
        return (
            (
                await self.session.execute(
                    select(ReportSchedule)
                    .where(
                        and_(
                            ReportSchedule.is_active.is_(True),
                            ReportSchedule.next_run_at.isnot(None),
                            ReportSchedule.next_run_at <= now_utc,
                        ),
                    )
                    .order_by(ReportSchedule.next_run_at.asc())
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def add(self, **fields: Any) -> ReportSchedule:
        row = ReportSchedule(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_run_times(
        self, *,
        schedule_id: UUID,
        last_run_at: datetime,
        next_run_at: datetime | None,
    ) -> None:
        await self.session.execute(
            update(ReportSchedule)
            .where(ReportSchedule.id == schedule_id)
            .values(last_run_at=last_run_at, next_run_at=next_run_at),
        )

    async def delete(self, schedule_id: UUID) -> int:
        result = await self.session.execute(
            delete(ReportSchedule).where(ReportSchedule.id == schedule_id),
        )
        return int(result.rowcount or 0)


# ============================================================
#  ReportSavedFilterRepository
# ============================================================
class ReportSavedFilterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, filter_id: UUID) -> ReportSavedFilter | None:
        return await self.session.get(ReportSavedFilter, filter_id)

    async def list_for_user_report(
        self, *, user_id: UUID, definition_id: UUID,
    ) -> Sequence[ReportSavedFilter]:
        return (
            (
                await self.session.execute(
                    select(ReportSavedFilter)
                    .where(
                        and_(
                            ReportSavedFilter.user_id == user_id,
                            ReportSavedFilter.report_definition_id == definition_id,
                        ),
                    )
                    .order_by(ReportSavedFilter.name),
                )
            )
            .scalars()
            .all()
        )

    async def get_default(
        self, *, user_id: UUID, definition_id: UUID,
    ) -> ReportSavedFilter | None:
        return (
            await self.session.execute(
                select(ReportSavedFilter)
                .where(
                    and_(
                        ReportSavedFilter.user_id == user_id,
                        ReportSavedFilter.report_definition_id == definition_id,
                        ReportSavedFilter.is_default.is_(True),
                    ),
                )
                .limit(1),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> ReportSavedFilter:
        row = ReportSavedFilter(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def clear_default(
        self, *, user_id: UUID, definition_id: UUID,
    ) -> None:
        await self.session.execute(
            update(ReportSavedFilter)
            .where(
                and_(
                    ReportSavedFilter.user_id == user_id,
                    ReportSavedFilter.report_definition_id == definition_id,
                ),
            )
            .values(is_default=False),
        )

    async def update_filters(
        self, *,
        filter_id: UUID,
        filters: dict[str, Any] | None,
        is_default: bool | None,
    ) -> int:
        values: dict[str, Any] = {}
        if filters is not None:
            values["filters_json"] = filters
        if is_default is not None:
            values["is_default"] = is_default
        if not values:
            return 0
        result = await self.session.execute(
            update(ReportSavedFilter)
            .where(ReportSavedFilter.id == filter_id)
            .values(**values),
        )
        return int(result.rowcount or 0)

    async def delete(self, filter_id: UUID) -> int:
        result = await self.session.execute(
            delete(ReportSavedFilter).where(ReportSavedFilter.id == filter_id),
        )
        return int(result.rowcount or 0)


# ============================================================
#  ReportFileRepository
# ============================================================
class ReportFileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, file_id: UUID) -> ReportFile | None:
        return await self.session.get(ReportFile, file_id)

    async def add(self, **fields: Any) -> ReportFile:
        row = ReportFile(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete_expired(
        self, *, now_utc: datetime, batch_size: int = 100,
    ) -> int:
        """Mark for deletion (returns count). The caller is responsible
        for unlinking the on-disk path before calling — we don't do
        FS deletes from inside a DB transaction.
        """
        ids = (
            (
                await self.session.execute(
                    select(ReportFile.id)
                    .where(ReportFile.expires_at <= now_utc)
                    .limit(batch_size),
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return 0
        await self.session.execute(
            delete(ReportFile).where(ReportFile.id.in_(ids)),
        )
        return len(ids)
