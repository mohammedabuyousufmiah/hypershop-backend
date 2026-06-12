"""ORM models for the rider routing module (Module 31).

Seven new tables that sit on top of the existing
``riders`` + ``delivery_assignments`` tables:

  rider_shifts              — per-day work session (start/end + GPS)
  rider_live_locations      — append-only GPS stream (one row per ping)
  run_sheets                — daily multi-stop bundle for a rider
  run_sheet_stops           — one row per stop, wraps ONE existing
                              delivery_assignment with planned + dynamic
                              sequencing, ETA, priority
  route_recalculation_logs  — append-only audit of why next stop changed
  route_eta_snapshots       — periodic ETA capture for SLA drift detection
  ops_route_overrides       — append-only audit of dispatcher actions

Migration 0025 also adds three columns to the existing ``riders`` table:
  - capacity_orders         (default 0; 0 = unlimited)
  - max_cash_limit_bdt      (default 0; 0 = no per-rider cap)
  - hub_code                (nullable; future hub-based dispatch)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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
#  rider_shifts
# ============================================================
class RiderShift(Base, TimestampMixin):
    __tablename__ = "rider_shifts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # The local calendar date the shift is *for*. Helpful for daily
    # rollups even when a shift crosses midnight.
    shift_date: Mapped[date] = mapped_column(Date, nullable=False)

    # ShiftStatus enum — see state.py + DB CHECK below.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="scheduled",
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # GPS pinpoints at shift start/end — bookend the day for dispute
    # resolution ("rider says they were on shift; where exactly?").
    start_latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7), nullable=True,
    )
    start_longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7), nullable=True,
    )
    end_latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7), nullable=True,
    )
    end_longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7), nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Prevent two open shifts for the same rider on the same date.
        # Two ENDED shifts on the same date are fine (e.g. morning +
        # evening split shifts).
        UniqueConstraint(
            "rider_id", "shift_date", "status",
            name="uq_rider_shifts_rider_date_open",
        ),
        CheckConstraint(
            "status IN ('scheduled','started','paused','ended')",
            name="ck_rider_shifts_status_enum",
        ),
        Index("ix_rider_shifts_rider_date", "rider_id", "shift_date"),
        Index(
            "ix_rider_shifts_open",
            "rider_id",
            postgresql_where=text("status IN ('started','paused')"),
        ),
    )


# ============================================================
#  rider_live_locations
# ============================================================
class RiderLiveLocation(Base):
    """Append-only GPS stream. One row per ping.

    DELIBERATELY no TimestampMixin — ``recorded_at`` is the device-side
    timestamp (what mattered to the rider) and ``ingested_at`` is the
    server-side stamp (what mattered to ops). The two diverge when the
    rider is offline and the app catches up later.
    """

    __tablename__ = "rider_live_locations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional — system pings (e.g. background fetch by an admin) won't
    # have a shift; rider-app pings always do.
    shift_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rider_shifts.id", ondelete="CASCADE"),
        nullable=True,
    )

    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)

    # Device-reported quality signals — kept as-is so we can audit the
    # incoming stream and tighten thresholds later.
    accuracy_meters: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True,
    )
    speed_kmh: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True,
    )
    heading_degrees: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True,
    )
    battery_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_mocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name="ck_rider_live_locations_lat_range",
        ),
        CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name="ck_rider_live_locations_lng_range",
        ),
        CheckConstraint(
            "accuracy_meters IS NULL OR accuracy_meters >= 0",
            name="ck_rider_live_locations_accuracy_nonneg",
        ),
        CheckConstraint(
            "battery_level IS NULL OR (battery_level >= 0 AND battery_level <= 100)",
            name="ck_rider_live_locations_battery_pct",
        ),
        # Hot-path index for "give me rider X's last N pings ordered
        # newest first".
        Index(
            "ix_rider_live_locations_rider_recorded",
            "rider_id", "recorded_at",
        ),
    )


# ============================================================
#  run_sheets
# ============================================================
class RunSheet(Base, TimestampMixin):
    __tablename__ = "run_sheets"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shift_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rider_shifts.id", ondelete="SET NULL"),
        nullable=True,
    )
    sheet_date: Mapped[date] = mapped_column(Date, nullable=False)

    # RunSheetStatus — see state.py + DB CHECK.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft",
    )

    # Cached counts — kept up-to-date by the service layer when a stop
    # transitions. Allows the rider home screen to render without a
    # full count(*) over stops.
    planned_stop_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    completed_stop_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    failed_stop_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    total_cod_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )

    # Freeze flag — set by ops via OpsRouteOverride. While frozen, the
    # next-stop engine returns the CURRENT next stop unchanged on every
    # call (no resequencing) until the freeze is lifted.
    sequence_frozen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','assigned','in_progress','completed','cancelled')",
            name="ck_run_sheets_status_enum",
        ),
        CheckConstraint(
            "planned_stop_count >= 0 AND completed_stop_count >= 0 "
            "AND failed_stop_count >= 0",
            name="ck_run_sheets_counts_nonneg",
        ),
        Index("ix_run_sheets_rider_date", "rider_id", "sheet_date"),
        Index(
            "ix_run_sheets_active",
            "rider_id",
            postgresql_where=text(
                "status IN ('assigned','in_progress')",
            ),
        ),
    )


# ============================================================
#  run_sheet_stops
# ============================================================
class RunSheetStop(Base, TimestampMixin):
    __tablename__ = "run_sheet_stops"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_sheet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("run_sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The bridge to the existing per-order delivery flow. POD/COD/etc.
    # all live on the assignment; this row only adds sequencing + ETA
    # metadata. UNIQUE so an assignment can only appear in ONE active
    # run sheet at a time.
    delivery_assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("delivery_assignments.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Location for routing — denormalised from the order's address so
    # the next-stop engine doesn't need to JOIN orders+addresses on
    # every recomputation.
    destination_latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7), nullable=True,
    )
    destination_longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7), nullable=True,
    )

    # Optional reference to the delivery zone the destination falls in
    # — used for same-zone-cluster bonus in the recommendation engine.
    delivery_zone_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )

    # Original order from dispatch.
    planned_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # Live order computed by the next-stop engine. NULL until the
    # first recompute.
    dynamic_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # StopStatus — see state.py + DB CHECK.
    stop_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="assigned",
    )

    # Stop-level attributes used by the recommendation engine.
    priority_level: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="normal",
    )
    order_type_tag: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="prepaid",
    )
    promised_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    promised_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Last estimate + last computed score. Snapshots are append-only
    # in route_eta_snapshots; this is just the *current* number for
    # the rider's home screen.
    estimated_travel_time_min: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    estimated_distance_km: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True,
    )
    recommendation_score: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True,
    )
    last_recomputed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Soft attributes that flow into the score.
    customer_available_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    requires_otp: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    requires_signature: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    cash_to_collect_bdt: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )

    __table_args__ = (
        # Keep the bridge to delivery_assignment unique — one stop per
        # assignment per run sheet.
        UniqueConstraint(
            "run_sheet_id", "delivery_assignment_id",
            name="uq_run_sheet_stops_sheet_assignment",
        ),
        # An assignment can appear in only ONE run sheet at a time
        # (across all sheets) — protect at the DB level with a partial
        # unique index where stop is non-terminal.
        Index(
            "uq_run_sheet_stops_assignment_active",
            "delivery_assignment_id",
            unique=True,
            postgresql_where=text(
                "stop_status IN ('assigned','ready','enroute','arrived')",
            ),
        ),
        CheckConstraint(
            "stop_status IN ('assigned','ready','enroute','arrived',"
            "'delivered','failed','skipped','cancelled')",
            name="ck_run_sheet_stops_status_enum",
        ),
        CheckConstraint(
            "priority_level IN ('low','normal','high','urgent')",
            name="ck_run_sheet_stops_priority_enum",
        ),
        CheckConstraint(
            "order_type_tag IN ('prepaid','cod','prescription',"
            "'cold_chain','mixed')",
            name="ck_run_sheet_stops_order_type_enum",
        ),
        CheckConstraint(
            "destination_latitude IS NULL OR "
            "(destination_latitude BETWEEN -90 AND 90)",
            name="ck_run_sheet_stops_lat_range",
        ),
        CheckConstraint(
            "destination_longitude IS NULL OR "
            "(destination_longitude BETWEEN -180 AND 180)",
            name="ck_run_sheet_stops_lng_range",
        ),
        CheckConstraint(
            "cash_to_collect_bdt >= 0",
            name="ck_run_sheet_stops_cash_nonneg",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_run_sheet_stops_attempts_nonneg",
        ),
        # Indexes for the two hot queries: render the rider's run sheet
        # in dynamic order, and find candidate stops for next-stop.
        Index(
            "ix_run_sheet_stops_sheet_status",
            "run_sheet_id", "stop_status",
        ),
        Index(
            "ix_run_sheet_stops_sheet_dynamic",
            "run_sheet_id", "dynamic_sequence",
        ),
    )


# ============================================================
#  route_recalculation_logs
# ============================================================
class RouteRecalculationLog(Base):
    """Append-only audit row for every next-stop recompute.

    Captures both the trigger (why we recomputed) and the result
    (whether the next-stop actually changed, and to what). Surfaces:
      - "rider's app keeps changing the recommendation every 10s"
        (look for high-frequency logs with no actual change)
      - "ops force-pushed a stop and we want the trail"
    """

    __tablename__ = "route_recalculation_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_sheet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("run_sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RecalcTrigger enum.
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    old_next_stop_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("run_sheet_stops.id", ondelete="SET NULL"),
        nullable=True,
    )
    new_next_stop_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("run_sheet_stops.id", ondelete="SET NULL"),
        nullable=True,
    )
    candidates_considered: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form payload (e.g. score breakdown for the chosen stop).
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('shift_start','location_update',"
            "'stop_completed','stop_failed','ops_override',"
            "'new_priority_order','manual_refresh')",
            name="ck_route_recalc_trigger_enum",
        ),
        Index(
            "ix_route_recalc_logs_sheet_time",
            "run_sheet_id", "created_at",
        ),
    )


# ============================================================
#  route_eta_snapshots
# ============================================================
class RouteEtaSnapshot(Base):
    """Append-only ETA capture per stop.

    Used to detect SLA drift over the day — if a stop's ETA keeps
    growing across snapshots, the rider is falling behind. Ops can set
    an alert threshold and react before the SLA is breached.
    """

    __tablename__ = "route_eta_snapshots"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_sheet_stop_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("run_sheet_stops.id", ondelete="CASCADE"),
        nullable=False,
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="CASCADE"),
        nullable=False,
    )
    eta_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_km: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False,
    )
    # Optional traffic level — populated only if the ETA source is a
    # live map provider that reports it.
    traffic_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "traffic_level IS NULL OR "
            "traffic_level IN ('low','moderate','high','severe')",
            name="ck_route_eta_traffic_enum",
        ),
        CheckConstraint(
            "eta_minutes >= 0",
            name="ck_route_eta_minutes_nonneg",
        ),
        Index(
            "ix_route_eta_snapshots_stop_time",
            "run_sheet_stop_id", "snapshot_at",
        ),
    )


# ============================================================
#  ops_route_overrides
# ============================================================
class OpsRouteOverride(Base):
    """Append-only audit of every dispatcher action against a route.

    Each row is one decision (force, freeze, skip, reassign). The actual
    *effect* on run-sheet state lives on the run_sheets / run_sheet_stops
    rows; this table is the trail.
    """

    __tablename__ = "ops_route_overrides"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_sheet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("run_sheets.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional — freeze/unfreeze act on the whole sheet, not a stop.
    run_sheet_stop_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("run_sheet_stops.id", ondelete="SET NULL"),
        nullable=True,
    )
    # OverrideType enum.
    override_type: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # Free-form payload (e.g. {"reassigned_to_rider_id": "..."}).
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        CheckConstraint(
            "override_type IN ('force_next_stop','freeze_sequence',"
            "'skip_stop','reassign_stop','unfreeze')",
            name="ck_ops_overrides_type_enum",
        ),
        Index(
            "ix_ops_overrides_sheet_time",
            "run_sheet_id", "created_at",
        ),
    )
