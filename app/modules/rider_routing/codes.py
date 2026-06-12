"""Audit action codes for the rider routing module."""

from __future__ import annotations

# ----- Shift -----
ACTION_SHIFT_STARTED = "rider.shift.started"
ACTION_SHIFT_PAUSED = "rider.shift.paused"
ACTION_SHIFT_RESUMED = "rider.shift.resumed"
ACTION_SHIFT_ENDED = "rider.shift.ended"

# ----- Location -----
ACTION_LOCATION_INGESTED = "rider.location.ingested"
ACTION_LOCATION_REJECTED = "rider.location.rejected"

# ----- Run sheet lifecycle -----
ACTION_RUN_SHEET_CREATED = "rider.run_sheet.created"
ACTION_RUN_SHEET_ASSIGNED = "rider.run_sheet.assigned"
ACTION_RUN_SHEET_CANCELLED = "rider.run_sheet.cancelled"
ACTION_RUN_SHEET_COMPLETED = "rider.run_sheet.completed"

# ----- Stop lifecycle -----
ACTION_STOP_STARTED = "rider.stop.started"
ACTION_STOP_ARRIVED = "rider.stop.arrived"
ACTION_STOP_DELIVERED = "rider.stop.delivered"
ACTION_STOP_FAILED = "rider.stop.failed"
ACTION_STOP_RESCHEDULED = "rider.stop.rescheduled"
ACTION_STOP_SKIPPED = "rider.stop.skipped"

# ----- Recommendation engine -----
ACTION_NEXT_STOP_RECOMPUTED = "rider.next_stop.recomputed"
ACTION_NEXT_STOP_CHANGED = "rider.next_stop.changed"

# ----- Ops overrides -----
ACTION_OVERRIDE_FORCE_NEXT = "rider.override.force_next"
ACTION_OVERRIDE_FREEZE = "rider.override.freeze"
ACTION_OVERRIDE_UNFREEZE = "rider.override.unfreeze"
ACTION_OVERRIDE_SKIP_STOP = "rider.override.skip_stop"
ACTION_OVERRIDE_REASSIGN = "rider.override.reassign"
