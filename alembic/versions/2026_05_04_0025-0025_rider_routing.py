"""Rider routing platform — 7 tables + ALTER riders.

Revision ID: 0025_rider_routing
Revises: 0024_reporting_platform
Create Date: 2026-05-04

Module 31. See ``app/modules/rider_routing/models.py`` for the
table-by-table docstrings.

ALTER riders:
  - capacity_orders         (default 0; 0 = unlimited)
  - max_cash_limit_bdt      (default 0; 0 = no per-rider cap)
  - hub_code                (nullable; future hub-based dispatch)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_rider_routing"
down_revision: str | Sequence[str] | None = "0024_reporting_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- ALTER riders (Module 12) -----------------------
    op.add_column(
        "riders",
        sa.Column(
            "capacity_orders", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "riders",
        sa.Column(
            "max_cash_limit_bdt", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "riders",
        sa.Column("hub_code", sa.String(64), nullable=True),
    )

    # ---------------- rider_shifts ----------------
    op.create_table(
        "rider_shifts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("shift_date", sa.Date, nullable=False),
        sa.Column(
            "status", sa.String(16),
            nullable=False, server_default="scheduled",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("start_longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("end_latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("end_longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint(
            "rider_id", "shift_date", "status",
            name="uq_rider_shifts_rider_date_open",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled','started','paused','ended')",
            name="ck_rider_shifts_status_enum",
        ),
    )
    op.create_index(
        "ix_rider_shifts_rider_date",
        "rider_shifts", ["rider_id", "shift_date"],
    )
    op.create_index(
        "ix_rider_shifts_open",
        "rider_shifts", ["rider_id"],
        postgresql_where=sa.text("status IN ('started','paused')"),
    )

    # ---------------- rider_live_locations ----------------
    op.create_table(
        "rider_live_locations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "shift_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rider_shifts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=False),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=False),
        sa.Column("accuracy_meters", sa.Numeric(8, 2), nullable=True),
        sa.Column("speed_kmh", sa.Numeric(8, 2), nullable=True),
        sa.Column("heading_degrees", sa.Numeric(8, 2), nullable=True),
        sa.Column("battery_level", sa.Integer, nullable=True),
        sa.Column(
            "is_mocked", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name="ck_rider_live_locations_lat_range",
        ),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name="ck_rider_live_locations_lng_range",
        ),
        sa.CheckConstraint(
            "accuracy_meters IS NULL OR accuracy_meters >= 0",
            name="ck_rider_live_locations_accuracy_nonneg",
        ),
        sa.CheckConstraint(
            "battery_level IS NULL OR (battery_level >= 0 AND battery_level <= 100)",
            name="ck_rider_live_locations_battery_pct",
        ),
    )
    op.create_index(
        "ix_rider_live_locations_rider_recorded",
        "rider_live_locations", ["rider_id", "recorded_at"],
    )

    # ---------------- run_sheets ----------------
    op.create_table(
        "run_sheets",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "shift_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rider_shifts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sheet_date", sa.Date, nullable=False),
        sa.Column(
            "status", sa.String(16),
            nullable=False, server_default="draft",
        ),
        sa.Column(
            "planned_stop_count", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "completed_stop_count", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "failed_stop_count", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "total_cod_amount", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "sequence_frozen", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
            "status IN ('draft','assigned','in_progress','completed','cancelled')",
            name="ck_run_sheets_status_enum",
        ),
        sa.CheckConstraint(
            "planned_stop_count >= 0 AND completed_stop_count >= 0 "
            "AND failed_stop_count >= 0",
            name="ck_run_sheets_counts_nonneg",
        ),
    )
    op.create_index(
        "ix_run_sheets_rider_date",
        "run_sheets", ["rider_id", "sheet_date"],
    )
    op.create_index(
        "ix_run_sheets_active",
        "run_sheets", ["rider_id"],
        postgresql_where=sa.text(
            "status IN ('assigned','in_progress')",
        ),
    )

    # ---------------- run_sheet_stops ----------------
    op.create_table(
        "run_sheet_stops",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_sheet_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "delivery_assignment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_assignments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("destination_latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("destination_longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("delivery_zone_code", sa.String(64), nullable=True),
        sa.Column("planned_sequence", sa.Integer, nullable=False),
        sa.Column("dynamic_sequence", sa.Integer, nullable=True),
        sa.Column(
            "stop_status", sa.String(16),
            nullable=False, server_default="assigned",
        ),
        sa.Column(
            "priority_level", sa.String(8),
            nullable=False, server_default="normal",
        ),
        sa.Column(
            "order_type_tag", sa.String(16),
            nullable=False, server_default="prepaid",
        ),
        sa.Column("promised_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promised_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_travel_time_min", sa.Integer, nullable=True),
        sa.Column("estimated_distance_km", sa.Numeric(8, 2), nullable=True),
        sa.Column("recommendation_score", sa.Numeric(10, 4), nullable=True),
        sa.Column("last_recomputed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "customer_available_flag", sa.Boolean,
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "requires_otp", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "requires_signature", sa.Boolean,
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "cash_to_collect_bdt", sa.Numeric(14, 2),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "attempt_count", sa.Integer,
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
        sa.UniqueConstraint(
            "run_sheet_id", "delivery_assignment_id",
            name="uq_run_sheet_stops_sheet_assignment",
        ),
        sa.CheckConstraint(
            "stop_status IN ('assigned','ready','enroute','arrived',"
            "'delivered','failed','skipped','cancelled')",
            name="ck_run_sheet_stops_status_enum",
        ),
        sa.CheckConstraint(
            "priority_level IN ('low','normal','high','urgent')",
            name="ck_run_sheet_stops_priority_enum",
        ),
        sa.CheckConstraint(
            "order_type_tag IN ('prepaid','cod','prescription',"
            "'cold_chain','mixed')",
            name="ck_run_sheet_stops_order_type_enum",
        ),
        sa.CheckConstraint(
            "destination_latitude IS NULL OR "
            "(destination_latitude BETWEEN -90 AND 90)",
            name="ck_run_sheet_stops_lat_range",
        ),
        sa.CheckConstraint(
            "destination_longitude IS NULL OR "
            "(destination_longitude BETWEEN -180 AND 180)",
            name="ck_run_sheet_stops_lng_range",
        ),
        sa.CheckConstraint(
            "cash_to_collect_bdt >= 0",
            name="ck_run_sheet_stops_cash_nonneg",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_run_sheet_stops_attempts_nonneg",
        ),
    )
    op.create_index(
        "uq_run_sheet_stops_assignment_active",
        "run_sheet_stops", ["delivery_assignment_id"],
        unique=True,
        postgresql_where=sa.text(
            "stop_status IN ('assigned','ready','enroute','arrived')",
        ),
    )
    op.create_index(
        "ix_run_sheet_stops_sheet_status",
        "run_sheet_stops", ["run_sheet_id", "stop_status"],
    )
    op.create_index(
        "ix_run_sheet_stops_sheet_dynamic",
        "run_sheet_stops", ["run_sheet_id", "dynamic_sequence"],
    )

    # ---------------- route_recalculation_logs ----------------
    op.create_table(
        "route_recalculation_logs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_sheet_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column(
            "old_next_stop_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_sheet_stops.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "new_next_stop_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_sheet_stops.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "candidates_considered", sa.Integer,
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "payload_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "trigger_type IN ('shift_start','location_update',"
            "'stop_completed','stop_failed','ops_override',"
            "'new_priority_order','manual_refresh')",
            name="ck_route_recalc_trigger_enum",
        ),
    )
    op.create_index(
        "ix_route_recalc_logs_sheet_time",
        "route_recalculation_logs", ["run_sheet_id", "created_at"],
    )

    # ---------------- route_eta_snapshots ----------------
    op.create_table(
        "route_eta_snapshots",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_sheet_stop_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_sheet_stops.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rider_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("eta_minutes", sa.Integer, nullable=False),
        sa.Column("distance_km", sa.Numeric(8, 2), nullable=False),
        sa.Column("traffic_level", sa.String(8), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "traffic_level IS NULL OR "
            "traffic_level IN ('low','moderate','high','severe')",
            name="ck_route_eta_traffic_enum",
        ),
        sa.CheckConstraint(
            "eta_minutes >= 0",
            name="ck_route_eta_minutes_nonneg",
        ),
    )
    op.create_index(
        "ix_route_eta_snapshots_stop_time",
        "route_eta_snapshots", ["run_sheet_stop_id", "snapshot_at"],
    )

    # ---------------- ops_route_overrides ----------------
    op.create_table(
        "ops_route_overrides",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_sheet_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_sheet_stop_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_sheet_stops.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("override_type", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload_json", postgresql.JSONB,
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "override_type IN ('force_next_stop','freeze_sequence',"
            "'skip_stop','reassign_stop','unfreeze')",
            name="ck_ops_overrides_type_enum",
        ),
    )
    op.create_index(
        "ix_ops_overrides_sheet_time",
        "ops_route_overrides", ["run_sheet_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ops_overrides_sheet_time", table_name="ops_route_overrides",
    )
    op.drop_table("ops_route_overrides")

    op.drop_index(
        "ix_route_eta_snapshots_stop_time",
        table_name="route_eta_snapshots",
    )
    op.drop_table("route_eta_snapshots")

    op.drop_index(
        "ix_route_recalc_logs_sheet_time",
        table_name="route_recalculation_logs",
    )
    op.drop_table("route_recalculation_logs")

    op.drop_index(
        "ix_run_sheet_stops_sheet_dynamic",
        table_name="run_sheet_stops",
    )
    op.drop_index(
        "ix_run_sheet_stops_sheet_status",
        table_name="run_sheet_stops",
    )
    op.drop_index(
        "uq_run_sheet_stops_assignment_active",
        table_name="run_sheet_stops",
    )
    op.drop_table("run_sheet_stops")

    op.drop_index("ix_run_sheets_active", table_name="run_sheets")
    op.drop_index("ix_run_sheets_rider_date", table_name="run_sheets")
    op.drop_table("run_sheets")

    op.drop_index(
        "ix_rider_live_locations_rider_recorded",
        table_name="rider_live_locations",
    )
    op.drop_table("rider_live_locations")

    op.drop_index("ix_rider_shifts_open", table_name="rider_shifts")
    op.drop_index("ix_rider_shifts_rider_date", table_name="rider_shifts")
    op.drop_table("rider_shifts")

    op.drop_column("riders", "hub_code")
    op.drop_column("riders", "max_cash_limit_bdt")
    op.drop_column("riders", "capacity_orders")
