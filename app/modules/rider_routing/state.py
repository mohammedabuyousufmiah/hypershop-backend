"""Enums + state-machine helpers for the rider routing module.

Module 31 sits OPERATIONALLY ABOVE the existing ``deliveries`` module:
  - ``deliveries.Rider`` already defines per-rider identity + status
    (offline / available / busy)
  - ``deliveries.DeliveryAssignment`` already tracks the per-order
    pickup → deliver → COD reconcile lifecycle

This module adds the *daily multi-stop bundle* concept on top:
  - a Shift starts/ends a workday
  - a RunSheet is the daily bundle of stops the rider works through
  - a RunSheetStop wraps ONE existing DeliveryAssignment with planned
    + dynamic sequencing, ETA, priority, and recommendation score

State machines kept deliberately separate from delivery_assignment's
state — a stop can be ``enroute`` while its underlying assignment is
still ``picked_up`` (the rider is heading there). Stop reaches
``delivered`` only when the assignment also transitions to delivered.
"""

from __future__ import annotations

from enum import StrEnum


# ----------------------------------------------------------------------
# Shift lifecycle
# ----------------------------------------------------------------------
class ShiftStatus(StrEnum):
    SCHEDULED = "scheduled"   # planned but not yet started
    STARTED = "started"       # rider clocked in; live
    PAUSED = "paused"         # break / fuel / lunch — location ingest still allowed
    ENDED = "ended"           # rider clocked out — terminal


SHIFT_TRANSITIONS: dict[ShiftStatus, frozenset[ShiftStatus]] = {
    ShiftStatus.SCHEDULED: frozenset({ShiftStatus.STARTED, ShiftStatus.ENDED}),
    ShiftStatus.STARTED: frozenset({ShiftStatus.PAUSED, ShiftStatus.ENDED}),
    ShiftStatus.PAUSED: frozenset({ShiftStatus.STARTED, ShiftStatus.ENDED}),
    ShiftStatus.ENDED: frozenset(),
}

# Statuses where location ingest is accepted. We allow PAUSED so the
# rider's last-known location is fresh when they resume.
SHIFT_LOCATION_OK: frozenset[ShiftStatus] = frozenset(
    {ShiftStatus.STARTED, ShiftStatus.PAUSED},
)


# ----------------------------------------------------------------------
# Run sheet lifecycle
# ----------------------------------------------------------------------
class RunSheetStatus(StrEnum):
    DRAFT = "draft"               # being built by dispatch — stops can be added
    ASSIGNED = "assigned"         # locked + handed to rider — read-only set of stops
    IN_PROGRESS = "in_progress"   # rider has at least one stop completed
    COMPLETED = "completed"       # all stops in terminal state — terminal
    CANCELLED = "cancelled"       # admin voided whole sheet — terminal


RUN_SHEET_TRANSITIONS: dict[RunSheetStatus, frozenset[RunSheetStatus]] = {
    RunSheetStatus.DRAFT: frozenset(
        {RunSheetStatus.ASSIGNED, RunSheetStatus.CANCELLED},
    ),
    RunSheetStatus.ASSIGNED: frozenset(
        {RunSheetStatus.IN_PROGRESS, RunSheetStatus.CANCELLED},
    ),
    RunSheetStatus.IN_PROGRESS: frozenset(
        {RunSheetStatus.COMPLETED, RunSheetStatus.CANCELLED},
    ),
    RunSheetStatus.COMPLETED: frozenset(),
    RunSheetStatus.CANCELLED: frozenset(),
}


# ----------------------------------------------------------------------
# Stop lifecycle (independent of underlying delivery_assignment status)
# ----------------------------------------------------------------------
class StopStatus(StrEnum):
    ASSIGNED = "assigned"       # in run sheet but rider hasn't started it
    READY = "ready"             # package loaded; rider can start any time
    ENROUTE = "enroute"         # rider has tapped "start" — heading there
    ARRIVED = "arrived"         # rider tapped "arrive" — at customer location
    DELIVERED = "delivered"     # delivery success (POD on assignment) — terminal
    FAILED = "failed"           # could not deliver — terminal
    SKIPPED = "skipped"         # ops override → skip — terminal
    CANCELLED = "cancelled"     # order cancelled mid-route — terminal


# Which stop statuses the next-stop engine considers as "candidates".
# Anything terminal is excluded; arrived/enroute are excluded because
# the rider is already committed to that stop.
STOP_CANDIDATE_STATUSES: frozenset[StopStatus] = frozenset(
    {StopStatus.ASSIGNED, StopStatus.READY},
)

STOP_TERMINAL_STATUSES: frozenset[StopStatus] = frozenset(
    {
        StopStatus.DELIVERED,
        StopStatus.FAILED,
        StopStatus.SKIPPED,
        StopStatus.CANCELLED,
    },
)

STOP_TRANSITIONS: dict[StopStatus, frozenset[StopStatus]] = {
    StopStatus.ASSIGNED: frozenset(
        {
            StopStatus.READY,
            StopStatus.ENROUTE,
            StopStatus.SKIPPED,
            StopStatus.CANCELLED,
        },
    ),
    StopStatus.READY: frozenset(
        {StopStatus.ENROUTE, StopStatus.SKIPPED, StopStatus.CANCELLED},
    ),
    StopStatus.ENROUTE: frozenset(
        {
            StopStatus.ARRIVED,
            StopStatus.FAILED,
            StopStatus.CANCELLED,
            # back to READY: rider rolled back ("started by mistake")
            StopStatus.READY,
        },
    ),
    StopStatus.ARRIVED: frozenset(
        {StopStatus.DELIVERED, StopStatus.FAILED, StopStatus.ENROUTE},
    ),
    StopStatus.DELIVERED: frozenset(),
    StopStatus.FAILED: frozenset(),
    StopStatus.SKIPPED: frozenset(),
    StopStatus.CANCELLED: frozenset(),
}


# ----------------------------------------------------------------------
# Stop attributes
# ----------------------------------------------------------------------
class PriorityLevel(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"           # narcotic / cold-chain / SLA-critical


PRIORITY_RANK = {
    PriorityLevel.LOW: 0,
    PriorityLevel.NORMAL: 1,
    PriorityLevel.HIGH: 2,
    PriorityLevel.URGENT: 3,
}


class OrderTypeTag(StrEnum):
    PREPAID = "prepaid"
    COD = "cod"
    PRESCRIPTION = "prescription"
    COLD_CHAIN = "cold_chain"
    MIXED = "mixed"


# ----------------------------------------------------------------------
# Recalculation triggers + override types
# ----------------------------------------------------------------------
class RecalcTrigger(StrEnum):
    SHIFT_START = "shift_start"
    LOCATION_UPDATE = "location_update"
    STOP_COMPLETED = "stop_completed"
    STOP_FAILED = "stop_failed"
    OPS_OVERRIDE = "ops_override"
    NEW_PRIORITY_ORDER = "new_priority_order"
    MANUAL_REFRESH = "manual_refresh"


class OverrideType(StrEnum):
    FORCE_NEXT_STOP = "force_next_stop"
    FREEZE_SEQUENCE = "freeze_sequence"
    SKIP_STOP = "skip_stop"
    REASSIGN_STOP = "reassign_stop"
    UNFREEZE = "unfreeze"


class TrafficLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    SEVERE = "severe"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
class TransitionError(Exception):
    """Raised when a state transition is not allowed.

    Carries the from/to so the API layer can surface a 409 with detail.
    """

    def __init__(self, *, frm: str, to: str, kind: str) -> None:
        self.frm = frm
        self.to = to
        self.kind = kind
        super().__init__(
            f"Invalid {kind} transition: {frm} → {to}",
        )


def assert_can_transition(
    *,
    kind: str,
    frm: str,
    to: str,
    allowed: dict,
) -> None:
    """Validate ``frm → to`` against the supplied allowed-map.

    Used by the service layer to reject illegal state writes early
    (instead of relying on a CHECK constraint at the DB layer to
    bounce them with a less actionable message).
    """
    try:
        legal = allowed[type(frm)(frm) if not isinstance(frm, str) else frm]
    except (KeyError, ValueError):
        raise TransitionError(frm=frm, to=to, kind=kind) from None
    legal_values = {s.value if hasattr(s, "value") else s for s in legal}
    if to not in legal_values:
        raise TransitionError(frm=frm, to=to, kind=kind)
