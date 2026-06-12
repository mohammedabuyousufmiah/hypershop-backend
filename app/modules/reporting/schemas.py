"""Pydantic v2 wire schemas for the reporting platform.

All requests/responses use :class:`StrictModel` (extra=forbid) so a
typo in a filter key fails fast instead of silently returning a wrong
result set.

The ``filters`` field is a free-form ``dict[str, Any]`` because each
report defines its own filter shape (in ``columns_json`` / docs).
Validation that's specific to one report happens inside that
report's builder.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ============================================================
#  /reports — list & detail
# ============================================================
class ReportListItem(StrictModel):
    id: UUID
    code: str
    name: str
    description: str
    category: str
    columns: list[dict[str, str]] = Field(default_factory=list)
    default_filters: dict[str, Any] = Field(default_factory=dict)
    allowed_export_formats: list[str] = Field(default_factory=list)
    max_rows_view: int
    max_rows_export: int
    can_view: bool
    can_export: bool
    can_schedule: bool
    scope_type: str


class ReportListResponse(StrictModel):
    items: list[ReportListItem]


# ============================================================
#  /reports/{code}/run
# ============================================================
class RunReportRequest(StrictModel):
    filters: dict[str, Any] = Field(default_factory=dict)


class RunReportResponse(StrictModel):
    code: str
    name: str
    columns: list[dict[str, str]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int
    scope_type: str
    filters_applied: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int


# ============================================================
#  /reports/{code}/export
# ============================================================
class ExportReportRequest(StrictModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    export_format: Literal["csv", "xlsx", "pdf"]


class ExportReportResponse(StrictModel):
    code: str
    name: str
    format: str
    file_id: UUID
    size_bytes: int
    row_count: int
    expires_at: datetime
    download_token: str


# ============================================================
#  Saved filters
# ============================================================
class SavedFilterCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    filters: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class SavedFilterUpdateRequest(StrictModel):
    filters: dict[str, Any] | None = None
    is_default: bool | None = None


class SavedFilterResponse(StrictModel):
    id: UUID
    report_definition_id: UUID
    user_id: UUID
    name: str
    filters: dict[str, Any] = Field(default_factory=dict)
    is_default: bool


class SavedFilterListResponse(StrictModel):
    items: list[SavedFilterResponse]


# ============================================================
#  Schedules
# ============================================================
class ScheduleCreateRequest(StrictModel):
    report_code: str = Field(min_length=1, max_length=120)
    frequency: Literal["daily", "weekly", "monthly"]
    run_hour_local: int = Field(ge=0, le=23)
    run_day_of_week: int | None = Field(default=None, ge=0, le=6)
    run_day_of_month: int | None = Field(default=None, ge=1, le=28)
    timezone_offset_hours: int = Field(default=6, ge=-12, le=14)
    export_format: Literal["csv", "xlsx", "pdf"]
    filters: dict[str, Any] = Field(default_factory=dict)
    recipient_emails: list[str] = Field(default_factory=list)


class ScheduleUpdateRequest(StrictModel):
    frequency: Literal["daily", "weekly", "monthly"] | None = None
    run_hour_local: int | None = Field(default=None, ge=0, le=23)
    run_day_of_week: int | None = Field(default=None, ge=0, le=6)
    run_day_of_month: int | None = Field(default=None, ge=1, le=28)
    timezone_offset_hours: int | None = Field(default=None, ge=-12, le=14)
    export_format: Literal["csv", "xlsx", "pdf"] | None = None
    filters: dict[str, Any] | None = None
    recipient_emails: list[str] | None = None
    is_active: bool | None = None


class ScheduleResponse(StrictModel):
    id: UUID
    report_definition_id: UUID
    report_code: str
    user_id: UUID
    frequency: str
    run_hour_local: int
    run_day_of_week: int | None
    run_day_of_month: int | None
    timezone_offset_hours: int
    export_format: str
    filters: dict[str, Any] = Field(default_factory=dict)
    recipient_emails: list[str] = Field(default_factory=list)
    is_active: bool
    next_run_at: datetime | None
    last_run_at: datetime | None


class ScheduleListResponse(StrictModel):
    items: list[ScheduleResponse]


# ============================================================
#  Admin: definitions + policies
# ============================================================
class DefinitionUpsertRequest(StrictModel):
    code: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=180)
    description: str = ""
    category: Literal[
        "operations", "finance", "sales", "inventory", "compliance",
        "executive",
    ]
    data_source: str = ""
    allowed_roles: list[str] = Field(default_factory=list)
    allowed_export_formats: list[Literal["csv", "xlsx", "pdf"]] = Field(
        default_factory=list,
    )
    default_filters: dict[str, Any] = Field(default_factory=dict)
    columns: list[dict[str, str]] = Field(default_factory=list)
    is_active: bool = True
    max_rows_view: int = Field(default=500, ge=1, le=10000)
    max_rows_export: int = Field(default=100000, ge=1, le=1000000)


class DefinitionResponse(StrictModel):
    id: UUID
    code: str
    name: str
    description: str
    category: str
    data_source: str
    allowed_roles: list[str]
    allowed_export_formats: list[str]
    default_filters: dict[str, Any]
    columns: list[dict[str, str]]
    is_active: bool
    max_rows_view: int
    max_rows_export: int


class DefinitionListResponse(StrictModel):
    items: list[DefinitionResponse]


class PolicyUpsertRequest(StrictModel):
    role_label: str = Field(min_length=1, max_length=64)
    scope_type: Literal["self", "team", "branch", "department", "global"]
    can_view: bool = True
    can_export: bool = False
    can_schedule: bool = False


class PolicyResponse(StrictModel):
    id: UUID
    report_definition_id: UUID
    role_label: str
    scope_type: str
    can_view: bool
    can_export: bool
    can_schedule: bool


class PolicyListResponse(StrictModel):
    items: list[PolicyResponse]


# ============================================================
#  Execution log
# ============================================================
class ExecutionLogResponse(StrictModel):
    id: UUID
    report_definition_id: UUID | None
    report_code: str
    user_id: UUID | None
    role_labels: list[str]
    execution_type: str
    status: str
    filters: dict[str, Any]
    row_count: int
    latency_ms: int
    file_id: UUID | None
    request_id: str
    error_message: str
    created_at: datetime


class ExecutionLogListResponse(StrictModel):
    items: list[ExecutionLogResponse]
