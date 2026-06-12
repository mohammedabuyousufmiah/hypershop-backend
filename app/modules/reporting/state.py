"""Enums for the reporting platform.

Kept in one place because the API, exporters, scheduler, and migration
all reference these constants. Adding a new value means editing here +
the matching DB CHECK constraint in alembic.
"""

from __future__ import annotations

from enum import StrEnum


class ReportCategory(StrEnum):
    """High-level grouping shown in the admin UI's left rail."""

    OPERATIONS = "operations"   # delivery, packing, COD
    FINANCE = "finance"         # P&L, trial balance, VAT, daily-close
    SALES = "sales"             # revenue, AOV, payment-method split
    INVENTORY = "inventory"     # stock buckets, expiry
    COMPLIANCE = "compliance"   # narcotic dispensing, audit trail
    EXECUTIVE = "executive"     # combined snapshot for owners


ALL_REPORT_CATEGORIES: frozenset[str] = frozenset(c.value for c in ReportCategory)


class ScopeType(StrEnum):
    """Row-level access scope.

    Hypershop today only ships ``self`` and ``global``; ``branch`` /
    ``team`` / ``department`` are accepted in the policy table for
    forward-compat (some report builders may already support them — see
    the ``apply_scope`` callbacks in ``builders/``).
    """

    SELF = "self"           # rows the current user owns/created
    TEAM = "team"           # rows owned by users in the same team
    BRANCH = "branch"       # rows from the user's branch
    DEPARTMENT = "department"
    GLOBAL = "global"       # all rows — admin/superuser only


ALL_SCOPE_TYPES: frozenset[str] = frozenset(s.value for s in ScopeType)


class ExportFormat(StrEnum):
    """Output format for the export pipeline.

    JSON is the in-line response from /run; CSV/XLSX/PDF write a file
    to disk and return a signed download URL.
    """

    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"
    PDF = "pdf"


ALL_EXPORT_FORMATS: frozenset[str] = frozenset(f.value for f in ExportFormat)
FILE_EXPORT_FORMATS: frozenset[str] = frozenset({
    ExportFormat.CSV,
    ExportFormat.XLSX,
    ExportFormat.PDF,
})


class ScheduleFrequency(StrEnum):
    DAILY = "daily"           # every day at run_hour_local
    WEEKLY = "weekly"         # every run_day_of_week (0=Mon..6=Sun)
    MONTHLY = "monthly"       # every run_day_of_month (1..28; >28 capped)


ALL_SCHEDULE_FREQUENCIES: frozenset[str] = frozenset(
    f.value for f in ScheduleFrequency
)


class ExecutionType(StrEnum):
    VIEW = "view"             # /run — JSON-only, paged for screen
    EXPORT = "export"         # /export — file produced, signed URL returned
    SCHEDULED = "scheduled"   # cron-driven — file emailed/stored


ALL_EXECUTION_TYPES: frozenset[str] = frozenset(e.value for e in ExecutionType)


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"         # permission check rejected the run


ALL_EXECUTION_STATUSES: frozenset[str] = frozenset(s.value for s in ExecutionStatus)
