"""ORM models for the reporting platform (Module 30).

Six tables work together to deliver a *central* reporting platform
rather than a sprawl of one-off admin endpoints:

  report_definitions     — registered reports (code, category, columns,
                           allowed_roles, allowed_export_formats). Seeded
                           at boot from ``bootstrap.py``; ops can add
                           rows later without a code deploy as long as
                           the ``code`` is wired to a builder in
                           ``builders/``.

  report_access_policies — fine-grained per-(report × role) policy:
                           can_view / can_export / can_schedule + the
                           default scope (self / branch / global). Acts
                           as a row-level filter applied by the builder.

  report_execution_logs  — every run/export/scheduled invocation. Used
                           for audit (who saw what) AND ops dashboards
                           ("which reports are slow / unused").

  report_schedules       — cron-driven recurring runs. The ARQ tick in
                           ``jobs.py`` polls due rows every 5 min,
                           runs each, recomputes ``next_run_at``.

  report_saved_filters   — per-user "favourite" filter sets so a busy
                           ops admin doesn't re-pick branch + date range
                           on every visit.

  report_files           — generated CSV/XLSX/PDF artifacts. Each row
                           tracks storage path, sha256, expires_at, and
                           is downloaded via signed URL (HMAC-SHA256
                           token validated in ``api/user.py``).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


# ============================================================
#  report_definitions
# ============================================================
class ReportDefinition(Base, TimestampMixin):
    __tablename__ = "report_definitions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Stable code referenced by the registry + URL paths. Lower-snake-
    # case + dot-namespaced (e.g. "finance.profit_and_loss"). Once
    # published, treat as immutable — renaming breaks saved filters
    # and schedules referring to it.
    code: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="",
    )
    # See ReportCategory enum + DB CHECK below.
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    # Free-form names the registry uses to look up a *builder* function
    # in ``builders/``. Lets the same code support multiple data sources
    # if we ever swap implementations (e.g. SQL → ClickHouse).
    data_source: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default="",
    )

    # Every roles array entry is a Hypershop RBAC role string (admin,
    # super_admin, finance, ops, doctor, ...). Empty list means "no role
    # has access" — useful for soft-disabling without deleting.
    allowed_roles_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    # Which export pipelines can be invoked for this report. Subset of
    # ExportFormat. JSON is implicit on /run.
    allowed_export_formats_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[\"csv\"]'::jsonb"),
    )
    # Defaults the API merges into a request when the user omits a
    # filter. Example: {"date_range_days": 30}.
    default_filters_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    # Ordered column metadata used by the serializer, exporters, and UI:
    # [{"key": "order_id", "label": "Order", "type": "string"}, ...]
    columns_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    group_by_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sort_by_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Soft-disable. Inactive reports are hidden from /reports list and
    # /run returns 403 (so already-saved schedules don't silently noop).
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    # Cap on how many rows the JSON /run response will return without
    # paging. Exports ignore this and stream all rows.
    max_rows_view: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("500"),
    )
    # Cap on export rows; defends against runaway full-table dumps.
    max_rows_export: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100000"),
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('operations','finance','sales','inventory',"
            "'compliance','executive')",
            name="ck_report_definitions_category_enum",
        ),
        CheckConstraint(
            "max_rows_view > 0 AND max_rows_view <= 10000",
            name="ck_report_definitions_max_rows_view_bounds",
        ),
        CheckConstraint(
            "max_rows_export > 0 AND max_rows_export <= 1000000",
            name="ck_report_definitions_max_rows_export_bounds",
        ),
        Index("ix_report_definitions_active_category", "is_active", "category"),
    )


# ============================================================
#  report_access_policies
# ============================================================
class ReportAccessPolicy(Base, TimestampMixin):
    __tablename__ = "report_access_policies"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    report_definition_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("report_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The Hypershop RBAC role this policy binds — single role per row.
    # Multi-role users get the union of their per-role permissions.
    role_label: Mapped[str] = mapped_column(String(64), nullable=False)
    # Default scope for this (report × role). Builders read this and
    # add a WHERE clause accordingly. See ScopeType enum.
    scope_type: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="self",
    )
    can_view: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    can_export: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    can_schedule: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )

    __table_args__ = (
        UniqueConstraint(
            "report_definition_id", "role_label",
            name="uq_report_access_policies_report_role",
        ),
        CheckConstraint(
            "scope_type IN ('self','team','branch','department','global')",
            name="ck_report_access_policies_scope_enum",
        ),
    )


# ============================================================
#  report_execution_logs
# ============================================================
class ReportExecutionLog(Base, TimestampMixin):
    __tablename__ = "report_execution_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # FK is SET NULL on delete so we can drop a defunct report
    # definition without losing the historical trail.
    report_definition_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("report_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Snapshot of the report code AT execution time — survives
    # report-deletion. Useful for grouping logs of long-renamed reports.
    report_code: Mapped[str] = mapped_column(String(120), nullable=False)

    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Snapshotted role(s) at run time. Important — user's role may
    # change later but THIS execution was authorised by the role they
    # held now.
    role_labels_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )

    # ExecutionType: view | export | scheduled.
    execution_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # ExecutionStatus: success | failed | denied.
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    # Filters as the user supplied them (post-default-merge). Stored
    # so we can replay an audit trail or debug "why did this run
    # return 0 rows".
    filters_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    row_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )

    # Optional link to a generated artifact.
    file_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("report_files.id", ondelete="SET NULL"),
        nullable=True,
    )

    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="",
    )
    # Truncated; full traceback lives in the structured log stream.
    error_message: Mapped[str] = mapped_column(
        String(1024), nullable=False, server_default="",
    )

    __table_args__ = (
        CheckConstraint(
            "execution_type IN ('view','export','scheduled')",
            name="ck_report_execution_logs_type_enum",
        ),
        CheckConstraint(
            "status IN ('success','failed','denied')",
            name="ck_report_execution_logs_status_enum",
        ),
        Index(
            "ix_report_execution_logs_report_created",
            "report_definition_id", "created_at",
        ),
        Index(
            "ix_report_execution_logs_user_created",
            "user_id", "created_at",
        ),
        # Helps the "find slow reports" ops query.
        Index(
            "ix_report_execution_logs_slow",
            "created_at",
            postgresql_where=text("latency_ms > 1000"),
        ),
    )


# ============================================================
#  report_schedules
# ============================================================
class ReportSchedule(Base, TimestampMixin):
    __tablename__ = "report_schedules"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    report_definition_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("report_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ScheduleFrequency: daily | weekly | monthly.
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    # Local-wall-clock hour (0..23) the run fires. Translated into UTC
    # on each compute_next_run pass using ``timezone_offset_hours``.
    run_hour_local: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("9"),
    )
    # 0..6 (Mon..Sun) for weekly. NULL for daily/monthly.
    run_day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 1..28 for monthly. NULL for daily/weekly. Capped at 28 to dodge
    # short-month skew (29/30/31 simply don't exist in February).
    run_day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Hours offset from UTC. BD = +6.
    timezone_offset_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("6"),
    )

    # ExportFormat to produce on each run. JSON is rejected — schedules
    # exist precisely to deliver a file.
    export_format: Mapped[str] = mapped_column(String(8), nullable=False)
    filters_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    # Optional list of email addresses to notify with the signed
    # download link once the file is ready. Frontend renders this as
    # comma-input.
    recipient_emails_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    # Set by ``compute_next_run`` after each tick. The cron picks rows
    # where ``is_active=true AND next_run_at <= now()`` so this index
    # is hot-pathed.
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "frequency IN ('daily','weekly','monthly')",
            name="ck_report_schedules_freq_enum",
        ),
        CheckConstraint(
            "export_format IN ('csv','xlsx','pdf')",
            name="ck_report_schedules_format_enum",
        ),
        CheckConstraint(
            "run_hour_local >= 0 AND run_hour_local <= 23",
            name="ck_report_schedules_hour_bounds",
        ),
        CheckConstraint(
            "(run_day_of_week IS NULL) OR "
            "(run_day_of_week >= 0 AND run_day_of_week <= 6)",
            name="ck_report_schedules_dow_bounds",
        ),
        CheckConstraint(
            "(run_day_of_month IS NULL) OR "
            "(run_day_of_month >= 1 AND run_day_of_month <= 28)",
            name="ck_report_schedules_dom_bounds",
        ),
        # Hot-pathed by jobs.py.
        Index(
            "ix_report_schedules_due",
            "next_run_at",
            postgresql_where=text("is_active = true"),
        ),
    )


# ============================================================
#  report_saved_filters
# ============================================================
class ReportSavedFilter(Base, TimestampMixin):
    __tablename__ = "report_saved_filters"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    report_definition_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("report_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # User-chosen label. Unique per (user, report) so the user can't
    # accidentally shadow their own filter.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    filters_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    # Star a single filter as the personal default — the API picks it
    # when the user hits /run with no filters.
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "report_definition_id", "name",
            name="uq_report_saved_filters_user_report_name",
        ),
    )


# ============================================================
#  report_files
# ============================================================
class ReportFile(Base, TimestampMixin):
    __tablename__ = "report_files"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Snapshot — survives report-definition deletion.
    report_code: Mapped[str] = mapped_column(String(120), nullable=False)
    # Owner — also the only principal allowed to download (alongside
    # admin/superuser). Enforced in api/user.py.
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # ExportFormat — csv | xlsx | pdf.
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    # Absolute path under ``settings.report_storage_dir``. Files are
    # written atomically (temp-file + rename) and never overwritten.
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    # SHA-256 of the file contents — used by the signed URL handler to
    # detect on-disk corruption + as a cache key for retries.
    sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="",
    )
    # Hard expiry — after this timestamp the signed URL is invalid and
    # a sweeper job (future) deletes the file.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # Stored so accidental re-downloads don't blow up disk via repeated
    # writes. Service uses (report_code, sha256) → existing file row.
    row_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )

    __table_args__ = (
        CheckConstraint(
            "format IN ('csv','xlsx','pdf')",
            name="ck_report_files_format_enum",
        ),
        Index("ix_report_files_user_created", "user_id", "created_at"),
        Index("ix_report_files_expires", "expires_at"),
    )
