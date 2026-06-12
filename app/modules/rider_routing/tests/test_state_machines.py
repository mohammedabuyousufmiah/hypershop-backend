"""State-machine validation tests — pure, no DB."""

from __future__ import annotations

import pytest

from app.modules.rider_routing.state import (
    SHIFT_TRANSITIONS,
    STOP_TRANSITIONS,
    RUN_SHEET_TRANSITIONS,
    ShiftStatus,
    StopStatus,
    RunSheetStatus,
    TransitionError,
    assert_can_transition,
)


# ---------------- Shift ----------------
@pytest.mark.parametrize(
    ("frm", "to", "ok"),
    [
        (ShiftStatus.SCHEDULED, ShiftStatus.STARTED, True),
        (ShiftStatus.STARTED, ShiftStatus.PAUSED, True),
        (ShiftStatus.PAUSED, ShiftStatus.STARTED, True),
        (ShiftStatus.STARTED, ShiftStatus.ENDED, True),
        (ShiftStatus.ENDED, ShiftStatus.STARTED, False),  # terminal
        (ShiftStatus.SCHEDULED, ShiftStatus.PAUSED, False),  # must start first
    ],
)
def test_shift_transitions(frm, to, ok):
    if ok:
        assert_can_transition(
            kind="shift",
            frm=frm.value, to=to.value,
            allowed=SHIFT_TRANSITIONS,
        )
    else:
        with pytest.raises(TransitionError):
            assert_can_transition(
                kind="shift",
                frm=frm.value, to=to.value,
                allowed=SHIFT_TRANSITIONS,
            )


# ---------------- Stop ----------------
@pytest.mark.parametrize(
    ("frm", "to", "ok"),
    [
        (StopStatus.ASSIGNED, StopStatus.READY, True),
        (StopStatus.ASSIGNED, StopStatus.ENROUTE, True),
        (StopStatus.READY, StopStatus.ENROUTE, True),
        (StopStatus.ENROUTE, StopStatus.ARRIVED, True),
        (StopStatus.ARRIVED, StopStatus.DELIVERED, True),
        (StopStatus.ARRIVED, StopStatus.FAILED, True),
        # Terminal — not allowed to go anywhere
        (StopStatus.DELIVERED, StopStatus.ARRIVED, False),
        (StopStatus.FAILED, StopStatus.READY, False),
        (StopStatus.SKIPPED, StopStatus.READY, False),
        (StopStatus.CANCELLED, StopStatus.ENROUTE, False),
        # Skip arrive: ENROUTE → DELIVERED is illegal
        (StopStatus.ENROUTE, StopStatus.DELIVERED, False),
    ],
)
def test_stop_transitions(frm, to, ok):
    if ok:
        assert_can_transition(
            kind="stop",
            frm=frm.value, to=to.value,
            allowed=STOP_TRANSITIONS,
        )
    else:
        with pytest.raises(TransitionError):
            assert_can_transition(
                kind="stop",
                frm=frm.value, to=to.value,
                allowed=STOP_TRANSITIONS,
            )


# ---------------- Run sheet ----------------
@pytest.mark.parametrize(
    ("frm", "to", "ok"),
    [
        (RunSheetStatus.DRAFT, RunSheetStatus.ASSIGNED, True),
        (RunSheetStatus.ASSIGNED, RunSheetStatus.IN_PROGRESS, True),
        (RunSheetStatus.IN_PROGRESS, RunSheetStatus.COMPLETED, True),
        (RunSheetStatus.IN_PROGRESS, RunSheetStatus.CANCELLED, True),
        (RunSheetStatus.COMPLETED, RunSheetStatus.CANCELLED, False),
        (RunSheetStatus.DRAFT, RunSheetStatus.IN_PROGRESS, False),  # skip ASSIGNED
    ],
)
def test_run_sheet_transitions(frm, to, ok):
    if ok:
        assert_can_transition(
            kind="run_sheet",
            frm=frm.value, to=to.value,
            allowed=RUN_SHEET_TRANSITIONS,
        )
    else:
        with pytest.raises(TransitionError):
            assert_can_transition(
                kind="run_sheet",
                frm=frm.value, to=to.value,
                allowed=RUN_SHEET_TRANSITIONS,
            )


def test_transition_error_carries_metadata():
    try:
        assert_can_transition(
            kind="stop",
            frm=StopStatus.DELIVERED.value,
            to=StopStatus.ARRIVED.value,
            allowed=STOP_TRANSITIONS,
        )
    except TransitionError as e:
        assert e.frm == StopStatus.DELIVERED.value
        assert e.to == StopStatus.ARRIVED.value
        assert e.kind == "stop"
    else:
        pytest.fail("Expected TransitionError")
