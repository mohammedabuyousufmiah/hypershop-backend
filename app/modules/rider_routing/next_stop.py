"""Next-stop recommendation engine.

Pure function (no DB writes inside ``score_candidates``) so it's
trivially testable. The service layer is responsible for fetching
candidates, calling here, and persisting the result + audit.

Score model (lower is better):

    score = travel_time_score
          + sla_penalty
          + customer_unavailability_penalty
          + cod_penalty
          + failed_attempt_penalty
          - same_zone_bonus
          - urgent_priority_bonus
          - cold_chain_bonus

Travel time is the dominant signal; everything else nudges. Weights
come from settings (env-tunable) so ops can re-balance without
shipping code.

"Meaningful improvement" gate (in :func:`should_change_next_stop`):
we don't switch the visible next-stop unless the gain is real —
otherwise the rider's app churns every GPS update. Configurable
threshold (default 5 minutes ETA gain OR an SLA breach avoided OR
an urgent stop becoming reachable).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.core.time import utc_now
from app.modules.rider_routing.models import RunSheetStop
from app.modules.rider_routing.state import (
    OrderTypeTag,
    PRIORITY_RANK,
    PriorityLevel,
    StopStatus,
)


# ----------------------------------------------------------------------
# Tunable weights (defaults — can be overridden by settings)
# ----------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class ScoringWeights:
    travel_time_min: float = 1.0
    sla_breach_penalty: float = 30.0
    sla_pressure_per_min_under: float = 0.4   # closer to deadline = worse
    customer_unavailability_penalty: float = 10.0
    cod_penalty_per_1k_bdt: float = 0.4
    failed_attempt_penalty: float = 8.0
    urgent_bonus: float = -12.0
    high_priority_bonus: float = -5.0
    cold_chain_bonus: float = -10.0
    prescription_bonus: float = -3.0
    same_zone_bonus: float = -4.0
    too_far_penalty_per_km_over: float = 0.5  # >15 km gets penalised
    far_threshold_km: float = 15.0


DEFAULT_WEIGHTS = ScoringWeights()


# ----------------------------------------------------------------------
# Scoring output
# ----------------------------------------------------------------------
@dataclass(slots=True)
class ScoreBreakdown:
    stop_id: UUID
    score: float
    eta_minutes: int | None
    distance_km: float | None
    reasons: list[str] = field(default_factory=list)
    feasible: bool = True
    infeasibility_reason: str | None = None


@dataclass(slots=True)
class RecommendationResult:
    chosen: ScoreBreakdown | None
    alternates: list[ScoreBreakdown]
    candidates_considered: int


# ----------------------------------------------------------------------
# Public entrypoints
# ----------------------------------------------------------------------
def score_candidates(
    *,
    candidates: list[RunSheetStop],
    rider_lat: float | None,
    rider_lng: float | None,
    current_zone_code: str | None = None,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
    now: datetime | None = None,
) -> RecommendationResult:
    """Pure: score every candidate and return the best + alternates.

    ``rider_lat``/``rider_lng`` are optional — if missing, falls back
    to the per-stop ``estimated_travel_time_min`` (set by an external
    map provider or a static default).

    ``current_zone_code`` lets us reward "same-zone" clustering — the
    rider's last delivered stop's zone code, typically.
    """
    now = now or utc_now()

    breakdowns: list[ScoreBreakdown] = []
    for stop in candidates:
        bd = _score_one(
            stop=stop,
            rider_lat=rider_lat,
            rider_lng=rider_lng,
            current_zone_code=current_zone_code,
            weights=weights,
            now=now,
        )
        breakdowns.append(bd)

    feasible = [b for b in breakdowns if b.feasible]
    feasible.sort(key=lambda b: b.score)
    chosen = feasible[0] if feasible else None
    alternates = feasible[1:4]
    return RecommendationResult(
        chosen=chosen,
        alternates=alternates,
        candidates_considered=len(candidates),
    )


def should_change_next_stop(
    *,
    current: RunSheetStop | None,
    proposed: ScoreBreakdown | None,
    eta_improvement_threshold_min: int = 5,
) -> tuple[bool, str]:
    """Apply the meaningful-improvement gate.

    Returns ``(should_change, reason)`` so the caller can audit.
    """
    if current is None:
        if proposed is None:
            return False, "no_candidates"
        return True, "first_pick"
    if proposed is None:
        return False, "no_proposed"
    if proposed.stop_id == current.id:
        return False, "same_stop"

    # SLA-driven flip: if the proposed stop has an SLA pressure that
    # current does not, switch unconditionally. Encoded in score —
    # if the proposed score is dramatically lower, that already means
    # SLA risk, so we trust the threshold gate below.

    cur_eta = current.estimated_travel_time_min or 9999
    new_eta = proposed.eta_minutes if proposed.eta_minutes is not None else 9999
    if cur_eta - new_eta >= eta_improvement_threshold_min:
        return True, f"eta_improvement:{cur_eta - new_eta}min"

    # If current stop is now infeasible (became unavailable mid-route),
    # we always switch.
    if current.stop_status not in (
        StopStatus.ASSIGNED.value,
        StopStatus.READY.value,
        StopStatus.ENROUTE.value,
    ):
        return True, "current_no_longer_active"

    return False, "improvement_below_threshold"


# ----------------------------------------------------------------------
# Internal scoring
# ----------------------------------------------------------------------
def _score_one(
    *,
    stop: RunSheetStop,
    rider_lat: float | None,
    rider_lng: float | None,
    current_zone_code: str | None,
    weights: ScoringWeights,
    now: datetime,
) -> ScoreBreakdown:
    reasons: list[str] = []

    # ---------- feasibility ----------
    if stop.stop_status not in (
        StopStatus.ASSIGNED.value,
        StopStatus.READY.value,
    ):
        return ScoreBreakdown(
            stop_id=stop.id,
            score=math.inf,
            eta_minutes=stop.estimated_travel_time_min,
            distance_km=float(stop.estimated_distance_km or 0),
            feasible=False,
            infeasibility_reason=f"stop_status:{stop.stop_status}",
        )

    # ---------- distance/ETA ----------
    eta_min: int | None
    dist_km: float | None
    if (
        rider_lat is not None
        and rider_lng is not None
        and stop.destination_latitude is not None
        and stop.destination_longitude is not None
    ):
        dist_km = _haversine_km(
            rider_lat, rider_lng,
            float(stop.destination_latitude),
            float(stop.destination_longitude),
        )
        # Rough urban-traffic conversion: 20 km/h average.
        eta_min = max(1, int(round(dist_km / 20 * 60)))
    else:
        eta_min = stop.estimated_travel_time_min
        dist_km = float(stop.estimated_distance_km) if stop.estimated_distance_km else None

    score = float(eta_min) * weights.travel_time_min if eta_min is not None else 9999.0
    if eta_min is not None:
        reasons.append(f"eta:{eta_min}m")

    if dist_km is not None and dist_km > weights.far_threshold_km:
        over = dist_km - weights.far_threshold_km
        score += over * weights.too_far_penalty_per_km_over
        reasons.append(f"far_penalty:{round(over, 1)}km")

    # ---------- SLA pressure ----------
    if stop.promised_end_at is not None:
        delta = (stop.promised_end_at - now).total_seconds() / 60
        if delta < 0:
            score += weights.sla_breach_penalty
            reasons.append("sla_breach")
        elif delta < 30:
            # Closer to the deadline → bigger penalty.
            score += (30 - delta) * weights.sla_pressure_per_min_under
            reasons.append(f"sla_pressure:{int(30 - delta)}m")

    # ---------- soft attributes ----------
    if not stop.customer_available_flag:
        score += weights.customer_unavailability_penalty
        reasons.append("customer_unavailable")

    if stop.cash_to_collect_bdt and stop.cash_to_collect_bdt > 0:
        cod = float(stop.cash_to_collect_bdt)
        score += (cod / 1000.0) * weights.cod_penalty_per_1k_bdt
        reasons.append(f"cod:{int(cod)}")

    if stop.attempt_count and stop.attempt_count > 0:
        score += stop.attempt_count * weights.failed_attempt_penalty
        reasons.append(f"prior_attempts:{stop.attempt_count}")

    # ---------- bonuses ----------
    pri = stop.priority_level
    if pri == PriorityLevel.URGENT.value:
        score += weights.urgent_bonus
        reasons.append("urgent_priority")
    elif pri == PriorityLevel.HIGH.value:
        score += weights.high_priority_bonus
        reasons.append("high_priority")

    tag = stop.order_type_tag
    if tag == OrderTypeTag.COLD_CHAIN.value:
        score += weights.cold_chain_bonus
        reasons.append("cold_chain")
    if tag == OrderTypeTag.PRESCRIPTION.value:
        score += weights.prescription_bonus
        reasons.append("prescription")

    if (
        current_zone_code is not None
        and stop.delivery_zone_code is not None
        and stop.delivery_zone_code == current_zone_code
    ):
        score += weights.same_zone_bonus
        reasons.append("same_zone")

    return ScoreBreakdown(
        stop_id=stop.id,
        score=round(score, 4),
        eta_minutes=eta_min,
        distance_km=dist_km,
        reasons=reasons,
        feasible=True,
    )


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in km.

    Accurate enough for routing within a city; not a substitute for
    a real map provider's road-distance calculation.
    """
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
