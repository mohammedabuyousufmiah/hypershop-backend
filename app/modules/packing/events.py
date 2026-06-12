from __future__ import annotations

EVT_PACKING_SESSION_OPENED = "packing.session.opened"
EVT_PACKING_SESSION_COMPLETED = "packing.session.completed"
EVT_PACKING_SESSION_CANCELLED = "packing.session.cancelled"
# Supervisor-override events are operationally interesting (audit + alerting)
EVT_PACKING_SUPERVISOR_OVERRIDE = "packing.scan.supervisor_override"
# Picker-blocked events surface to a supervisor dashboard so they can intervene
EVT_PACKING_BLOCKED = "packing.scan.blocked"
