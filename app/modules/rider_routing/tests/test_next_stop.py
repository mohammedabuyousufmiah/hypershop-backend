"""Pure-function tests for the next-stop scoring engine."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.rider_routing.next_stop import (
    DEFAULT_WEIGHTS,
    score_candidates,
    should_change_next_stop,
)
from app.modules.rider_routing.state import (
    OrderTypeTag,
    PriorityLevel,
    StopStatus,
)


def _stop(
    *,
    eta=10,
    dist_km=2.0,
    priority=PriorityLevel.NORMAL,
    cod=Decimal("0"),
    status=StopStatus.READY,
    available=True,
    attempts=0,
    tag=OrderTypeTag.PREPAID,
    promised_end_at=None,
    zone="A",
    dest_lat=23.81,
    dest_lng=90.41,
):
    return SimpleNamespace(
        id=uuid4(),
        stop_status=status.value,
        priority_level=priority.value,
        order_type_tag=tag.value,
        promised_start_at=None,
        promised_end_at=promised_end_at,
        estimated_travel_time_min=eta,
        estimated_distance_km=Decimal(str(dist_km)),
        cash_to_collect_bdt=cod,
        attempt_count=attempts,
        customer_available_flag=available,
        destination_latitude=Decimal(str(dest_lat)),
        destination_longitude=Decimal(str(dest_lng)),
        delivery_zone_code=zone,
    )


def test_chosen_is_lowest_score():
    far = _stop(eta=30)
    near = _stop(eta=5)
    result = score_candidates(
        candidates=[far, near], rider_lat=None, rider_lng=None,
    )
    assert result.chosen.stop_id == near.id
    assert len(result.alternates) == 1
    assert result.alternates[0].stop_id == far.id


def test_terminal_status_is_infeasible():
    delivered = _stop(status=StopStatus.DELIVERED)
    available = _stop(status=StopStatus.READY)
    result = score_candidates(
        candidates=[delivered, available],
        rider_lat=None, rider_lng=None,
    )
    assert result.chosen.stop_id == available.id
    # Delivered stop didn't make alternates — it's filtered out.
    assert all(a.stop_id != delivered.id for a in result.alternates)


def test_urgent_priority_beats_higher_eta():
    fast_normal = _stop(eta=5, priority=PriorityLevel.NORMAL)
    slow_urgent = _stop(eta=15, priority=PriorityLevel.URGENT)
    result = score_candidates(
        candidates=[fast_normal, slow_urgent],
        rider_lat=None, rider_lng=None,
    )
    # 15 - 12 = 3 vs 5 normal → urgent wins
    assert result.chosen.stop_id == slow_urgent.id


def test_unavailable_customer_drops_to_alternate():
    available = _stop(eta=10, available=True)
    unavailable = _stop(eta=5, available=False)
    result = score_candidates(
        candidates=[available, unavailable],
        rider_lat=None, rider_lng=None,
    )
    # 5 + 10 (unavailability) = 15 vs 10 → available wins
    assert result.chosen.stop_id == available.id


def test_failed_attempts_penalty_applies():
    fresh = _stop(eta=10, attempts=0)
    bounced_twice = _stop(eta=8, attempts=2)
    result = score_candidates(
        candidates=[fresh, bounced_twice],
        rider_lat=None, rider_lng=None,
    )
    # 8 + 2*8 = 24 vs 10 → fresh wins
    assert result.chosen.stop_id == fresh.id


def test_sla_breach_penalty_applied():
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    breached = _stop(eta=5, promised_end_at=past)
    result = score_candidates(
        candidates=[breached], rider_lat=None, rider_lng=None,
    )
    assert "sla_breach" in result.chosen.reasons


def test_same_zone_bonus_helps_clustering():
    in_zone = _stop(eta=10, zone="A")
    other_zone = _stop(eta=8, zone="B")
    result = score_candidates(
        candidates=[in_zone, other_zone],
        rider_lat=None, rider_lng=None,
        current_zone_code="A",
    )
    # 10 + (-4) = 6 vs 8 → in_zone wins
    assert result.chosen.stop_id == in_zone.id
    assert "same_zone" in result.chosen.reasons


def test_haversine_used_when_rider_position_known():
    # Two stops at same ETA but different actual lat/lng. Haversine
    # should override the static estimate.
    near = _stop(eta=99, dest_lat=23.81, dest_lng=90.41)  # rider sits here
    far = _stop(eta=99, dest_lat=23.85, dest_lng=90.45)
    result = score_candidates(
        candidates=[near, far],
        rider_lat=23.81, rider_lng=90.41,
    )
    assert result.chosen.stop_id == near.id


def test_should_change_blocks_below_threshold():
    current = _stop(eta=10)
    # Same stop_id passed as proposed → no change
    from app.modules.rider_routing.next_stop import ScoreBreakdown
    proposed = ScoreBreakdown(
        stop_id=current.id, score=8, eta_minutes=8, distance_km=1.0,
    )
    changed, reason = should_change_next_stop(
        current=current, proposed=proposed,
    )
    assert not changed
    assert "same_stop" in reason


def test_should_change_allows_meaningful_improvement():
    current = _stop(eta=20)
    from app.modules.rider_routing.next_stop import ScoreBreakdown
    proposed = ScoreBreakdown(
        stop_id=uuid4(), score=5, eta_minutes=5, distance_km=1.0,
    )
    changed, reason = should_change_next_stop(
        current=current, proposed=proposed,
    )
    assert changed
    assert "eta_improvement" in reason


def test_should_change_first_pick_when_no_current():
    from app.modules.rider_routing.next_stop import ScoreBreakdown
    proposed = ScoreBreakdown(
        stop_id=uuid4(), score=5, eta_minutes=5, distance_km=1.0,
    )
    changed, reason = should_change_next_stop(
        current=None, proposed=proposed,
    )
    assert changed
    assert reason == "first_pick"


def test_no_candidates_returns_none():
    result = score_candidates(
        candidates=[], rider_lat=None, rider_lng=None,
    )
    assert result.chosen is None
    assert result.candidates_considered == 0
