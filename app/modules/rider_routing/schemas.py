"""Pydantic v2 wire schemas for the rider routing module."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ============================================================
#  Shift
# ============================================================
class ShiftStartRequest(StrictModel):
    rider_id: UUID
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    notes: str | None = Field(default=None, max_length=2000)


class ShiftEndRequest(StrictModel):
    end_latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    end_longitude: Decimal | None = Field(default=None, ge=-180, le=180)


class ShiftResponse(StrictModel):
    id: UUID
    rider_id: UUID
    shift_date: date
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    start_latitude: Decimal | None
    start_longitude: Decimal | None
    end_latitude: Decimal | None
    end_longitude: Decimal | None


# ============================================================
#  Location
# ============================================================
class LocationUpdateRequest(StrictModel):
    rider_id: UUID
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)
    accuracy_meters: Decimal | None = Field(default=None, ge=0)
    speed_kmh: Decimal | None = Field(default=None, ge=0, le=300)
    heading_degrees: Decimal | None = Field(default=None, ge=0, lt=360)
    battery_level: int | None = Field(default=None, ge=0, le=100)
    is_mocked: bool = False
    recorded_at: datetime


class LocationUpdateResponse(StrictModel):
    accepted: bool
    next_recompute_in_seconds: int
    last_known_at: datetime


class LiveMapEntry(StrictModel):
    rider_id: UUID
    latitude: Decimal
    longitude: Decimal
    speed_kmh: Decimal | None
    heading_degrees: Decimal | None
    battery_level: int | None
    recorded_at: datetime


class LiveMapResponse(StrictModel):
    items: list[LiveMapEntry]
    since: datetime


# ============================================================
#  Run sheet + stops
# ============================================================
class StopSummary(StrictModel):
    id: UUID
    delivery_assignment_id: UUID
    planned_sequence: int
    dynamic_sequence: int | None
    stop_status: str
    priority_level: str
    order_type_tag: str
    promised_start_at: datetime | None
    promised_end_at: datetime | None
    estimated_travel_time_min: int | None
    estimated_distance_km: Decimal | None
    cash_to_collect_bdt: Decimal
    requires_otp: bool
    requires_signature: bool
    customer_available_flag: bool
    attempt_count: int
    destination_latitude: Decimal | None
    destination_longitude: Decimal | None
    delivery_zone_code: str | None


class RunSheetResponse(StrictModel):
    id: UUID
    rider_id: UUID
    shift_id: UUID | None
    sheet_date: date
    status: str
    planned_stop_count: int
    completed_stop_count: int
    failed_stop_count: int
    total_cod_amount: Decimal
    sequence_frozen: bool
    notes: str | None
    stops: list[StopSummary] = Field(default_factory=list)


class CurrentRunSheetResponse(StrictModel):
    run_sheet: RunSheetResponse | None
    next_stop_id: UUID | None


# ============================================================
#  Next-stop recommendation
# ============================================================
class NextStopReason(StrictModel):
    stop_id: UUID
    score: float
    eta_minutes: int | None
    distance_km: float | None
    reasons: list[str]


class NextStopResponse(StrictModel):
    chosen: NextStopReason | None
    alternates: list[NextStopReason]
    candidates_considered: int
    changed: bool


# ============================================================
#  Stop lifecycle
# ============================================================
class StopActionRequest(StrictModel):
    rider_id: UUID


class StopCompleteRequest(StrictModel):
    rider_id: UUID
    cash_collected_bdt: Decimal | None = Field(default=None, ge=0)


class StopFailRequest(StrictModel):
    rider_id: UUID
    reason: str = Field(min_length=1, max_length=512)


class StopRescheduleRequest(StrictModel):
    rider_id: UUID
    promised_start_at: datetime | None = None
    promised_end_at: datetime | None = None


class StopResponse(StrictModel):
    id: UUID
    run_sheet_id: UUID
    delivery_assignment_id: UUID
    stop_status: str
    attempt_count: int
    last_recomputed_at: datetime | None


# ============================================================
#  Admin: list + create + override
# ============================================================
class CreateRunSheetRequest(StrictModel):
    rider_id: UUID
    sheet_date: date
    delivery_assignment_ids: list[UUID] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=2000)


class RunSheetListItem(StrictModel):
    id: UUID
    rider_id: UUID
    sheet_date: date
    status: str
    planned_stop_count: int
    completed_stop_count: int
    failed_stop_count: int


class RunSheetListResponse(StrictModel):
    items: list[RunSheetListItem]


class ForceNextStopRequest(StrictModel):
    target_stop_id: UUID
    reason: str = Field(min_length=1, max_length=512)
    expires_at: datetime | None = None


class FreezeRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=512)
    expires_at: datetime | None = None


class UnfreezeRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=512)


class SkipStopRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=512)


class ReassignStopRequest(StrictModel):
    target_run_sheet_id: UUID
    reason: str = Field(min_length=1, max_length=512)


class OverrideResponse(StrictModel):
    id: UUID
    run_sheet_id: UUID
    run_sheet_stop_id: UUID | None
    override_type: str
    reason: str
    created_at: datetime
    expires_at: datetime | None
    payload: dict[str, Any] = Field(default_factory=dict)


class OverrideListResponse(StrictModel):
    items: list[OverrideResponse]


# ============================================================
#  Audit feeds
# ============================================================
class RecalcLogResponse(StrictModel):
    id: UUID
    run_sheet_id: UUID
    rider_id: UUID
    trigger_type: str
    old_next_stop_id: UUID | None
    new_next_stop_id: UUID | None
    candidates_considered: int
    reason: str | None
    payload: dict[str, Any]
    created_at: datetime


class RecalcLogListResponse(StrictModel):
    items: list[RecalcLogResponse]
