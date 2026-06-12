"""Reporting platform — 6 tables for the central report registry.

Revision ID: 0024_reporting_platform
Revises: 0023_search
Create Date: 2026-05-04

Module 30. See ``app/modules/reporting/models.py`` for table-by-table
docstrings. Order matters for FK creation: report_files MUST exist
before report_execution_logs (which has file_id FK).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_reporting_platform"
down_revision: str | Sequence[str] | None = "0023_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- report_definitions ----------------
    op.create_table(
        "report_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column(
            "data_source", sa.String(120), nullable=False, server_default="",
        ),
        sa.Column(
            "allowed_roles_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "allowed_export_formats_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'[\"csv\"]'::jsonb"),
        ),
        sa.Column(
            "default_filters_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "columns_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("group_by_json", postgresql.JSONB, nullable=True),
        sa.Column("sort_by_json", postgresql.JSONB, nullable=True),
        sa.Column(
            "is_active", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "max_rows_view", sa.Integer,
            nullable=False, server_default=sa.text("500"),
        ),
        sa.Column(
            "max_rows_export", sa.Integer,
            nullable=False, server_default=sa.text("100000"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint("code", name="uq_report_definitions_code"),
        sa.CheckConstraint(
            "category IN ('operations','finance','sales','inventory',"
            "'compliance','executive')",
            name="ck_report_definitions_category_enum",
        ),
        sa.CheckConstraint(
            "max_rows_view > 0 AND max_rows_view <= 10000",
            name="ck_report_definitions_max_rows_view_bounds",
        ),
        sa.CheckConstraint(
            "max_rows_export > 0 AND max_rows_export <= 1000000",
            name="ck_report_definitions_max_rows_export_bounds",
        ),
    )
    op.create_index(
        "ix_report_definitions_active_category",
        "report_definitions",
        ["is_active", "category"],
    )

    # ---------------- report_access_policies ----------------
    op.create_table(
        "report_access_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "report_definition_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_label", sa.String(64), nullable=False),
        sa.Column(
            "scope_type", sa.String(24),
            nullable=False, server_default="self",
        ),
        sa.Column(
            "can_view", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "can_export", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "can_schedule", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint(
            "report_definition_id", "role_label",
            name="uq_report_access_policies_report_role",
        ),
        sa.CheckConstraint(
            "scope_type IN ('self','team','branch','department','global')",
            name="ck_report_access_policies_scope_enum",
        ),
    )

    # ---------------- report_files (must precede execution_logs FK) -----
    op.create_table(
        "report_files",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("report_code", sa.String(120), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column(
            "size_bytes", sa.BigInteger,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "sha256", sa.String(64), nullable=False, server_default="",
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False,
        ),
        sa.Column(
            "row_count", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "format IN ('csv','xlsx','pdf')",
            name="ck_report_files_format_enum",
        ),
    )
    op.create_index(
        "ix_report_files_user_created",
        "report_files",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_report_files_expires",
        "report_files",
        ["expires_at"],
    )

    # ---------------- report_execution_logs ----------------
    op.create_table(
        "report_execution_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "report_definition_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_definitions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("report_code", sa.String(120), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "role_labels_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("execution_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "filters_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "row_count", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "latency_ms", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "file_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "request_id", sa.String(64), nullable=False, server_default="",
        ),
        sa.Column(
            "error_message", sa.String(1024),
            nullable=False, server_default="",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "execution_type IN ('view','export','scheduled')",
            name="ck_report_execution_logs_type_enum",
        ),
        sa.CheckConstraint(
            "status IN ('success','failed','denied')",
            name="ck_report_execution_logs_status_enum",
        ),
    )
    op.create_index(
        "ix_report_execution_logs_report_created",
        "report_execution_logs",
        ["report_definition_id", "created_at"],
    )
    op.create_index(
        "ix_report_execution_logs_user_created",
        "report_execution_logs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_report_execution_logs_slow",
        "report_execution_logs",
        ["created_at"],
        postgresql_where=sa.text("latency_ms > 1000"),
    )

    # ---------------- report_schedules ----------------
    op.create_table(
        "report_schedules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "report_definition_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column(
            "run_hour_local", sa.Integer,
            nullable=False, server_default=sa.text("9"),
        ),
        sa.Column("run_day_of_week", sa.Integer, nullable=True),
        sa.Column("run_day_of_month", sa.Integer, nullable=True),
        sa.Column(
            "timezone_offset_hours", sa.Integer,
            nullable=False, server_default=sa.text("6"),
        ),
        sa.Column("export_format", sa.String(8), nullable=False),
        sa.Column(
            "filters_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "recipient_emails_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_active", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "next_run_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "last_run_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "frequency IN ('daily','weekly','monthly')",
            name="ck_report_schedules_freq_enum",
        ),
        sa.CheckConstraint(
            "export_format IN ('csv','xlsx','pdf')",
            name="ck_report_schedules_format_enum",
        ),
        sa.CheckConstraint(
            "run_hour_local >= 0 AND run_hour_local <= 23",
            name="ck_report_schedules_hour_bounds",
        ),
        sa.CheckConstraint(
            "(run_day_of_week IS NULL) OR "
            "(run_day_of_week >= 0 AND run_day_of_week <= 6)",
            name="ck_report_schedules_dow_bounds",
        ),
        sa.CheckConstraint(
            "(run_day_of_month IS NULL) OR "
            "(run_day_of_month >= 1 AND run_day_of_month <= 28)",
            name="ck_report_schedules_dom_bounds",
        ),
    )
    op.create_index(
        "ix_report_schedules_due",
        "report_schedules",
        ["next_run_at"],
        postgresql_where=sa.text("is_active = true"),
    )

    # ---------------- report_saved_filters ----------------
    op.create_table(
        "report_saved_filters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "report_definition_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("report_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "filters_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_default", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint(
            "user_id", "report_definition_id", "name",
            name="uq_report_saved_filters_user_report_name",
        ),
    )


def downgrade() -> None:
    op.drop_table("report_saved_filters")
    op.drop_index("ix_report_schedules_due", table_name="report_schedules")
    op.drop_table("report_schedules")
    op.drop_index(
        "ix_report_execution_logs_slow",
        table_name="report_execution_logs",
    )
    op.drop_index(
        "ix_report_execution_logs_user_created",
        table_name="report_execution_logs",
    )
    op.drop_index(
        "ix_report_execution_logs_report_created",
        table_name="report_execution_logs",
    )
    op.drop_table("report_execution_logs")
    op.drop_index("ix_report_files_expires", table_name="report_files")
    op.drop_index("ix_report_files_user_created", table_name="report_files")
    op.drop_table("report_files")
    op.drop_table("report_access_policies")
    op.drop_index(
        "ix_report_definitions_active_category",
        table_name="report_definitions",
    )
    op.drop_table("report_definitions")
