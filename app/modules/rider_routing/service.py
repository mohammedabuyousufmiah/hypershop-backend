"""Rider-routing orchestration service.

Three concerns held in one class:
  - shift lifecycle (start / pause / resume / end)
  - location ingest + freshness validation
  - stop lifecycle (start / arrive / complete / fail / reschedule)
  - next-stop recommendation (delegates to next_stop.score_candidates)

Cross-cutting: every state-changing method writes an audit row + logs
a recalc when relevant. The dispatcher-side override service lives in
``override_service.py`` to keep this file focused on rider actions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import ServiceUnavailableError
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.deliveries.models import Rider
from app.modules.rider_routing import codes, next_stop, state
from app.modules.rider_routing.errors import (
    IllegalStopTransitionError,
    MockedLocationRejectedError,
    NoCandidateStopsError,
    RiderNotFoundError,
    RunSheetFrozenError,
    RunSheetNotFoundError,
    ShiftAlreadyOpenError,
    ShiftClosedError,
    ShiftNotFoundError,
    StaleLocationError,
    StopNotForRiderError,
    StopNotFoundError,
)
from app.modules.rider_routing.next_stop import (
    DEFAULT_WEIGHTS,
    RecommendationResult,
)
from app.modules.rider_routing.repository import (
    RiderLiveLocationRepository,
    RiderShiftRepository,
    RouteRecalculationLogRepository,
    RunSheetRepository,
    RunSheetStopRepository,
)
from app.modules.rider_routing.state import (
    SHIFT_LOCATION_OK,
    STOP_TRANSITIONS,
    RecalcTrigger,
    RunSheetStatus,
    ShiftStatus,
    StopStatus,
)

_log = get_logger("hypershop.rider_routing.service")


class RiderRoutingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shifts = RiderShiftRepository(session)
        self.locations = RiderLiveLocationRepository(session)
        self.run_sheets = RunSheetRepository(session)
        self.stops = RunSheetStopRepository(session)
        self.recalc_logs = RouteRecalculationLogRepository(session)

    # ==================================================================
    # SHIFT LIFECYCLE
    # ==================================================================
    async def start_shift(
        self, *,
        rider_id: UUID,
        latitude: Decimal | None,
        longitude: Decimal | None,
        principal: Principal | SystemPrincipal,
        notes: str | None = None,
    ):
        await self._assert_rider_exists(rider_id)
        existing = await self.shifts.get_open_for_rider(rider_id)
        if existing is not None:
            raise ShiftAlreadyOpenError(
                f"Rider already has an open shift {existing.id}.",
            )

        # Module 32 — wallet eligibility gate. Blocks shift start if
        # the rider has unpaid COD from a previous day with no active
        # carry-forward. Wallet-less riders pass through silently.
        from app.modules.rider_wallet.service import (
            AssignmentEligibilityService,
        )
        await AssignmentEligibilityService(
            self.session,
        ).assert_or_raise(rider_id=rider_id)

        now = utc_now()
        row = await self.shifts.add(
            rider_id=rider_id,
            shift_date=date(now.year, now.month, now.day),
            status=ShiftStatus.STARTED.value,
            started_at=now,
            start_latitude=latitude,
            start_longitude=longitude,
            notes=notes,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_SHIFT_STARTED,
            resource_type="rider_shift",
            resource_id=row.id,
            metadata={
                "rider_id": str(rider_id),
                "has_gps": latitude is not None and longitude is not None,
            },
        )
        return row

    async def transition_shift(
        self, *,
        shift_id: UUID,
        to_status: ShiftStatus,
        principal: Principal | SystemPrincipal,
        end_latitude: Decimal | None = None,
        end_longitude: Decimal | None = None,
    ):
        shift = await self.shifts.get(shift_id)
        if shift is None:
            raise ShiftNotFoundError("Shift not found.")
        from app.modules.rider_routing.state import (
            SHIFT_TRANSITIONS,
            assert_can_transition,
        )
        assert_can_transition(
            kind="shift",
            frm=shift.status,
            to=to_status.value,
            allowed=SHIFT_TRANSITIONS,
        )
        ended_at = utc_now() if to_status == ShiftStatus.ENDED else None
        await self.shifts.update_status(
            shift_id=shift.id,
            status=to_status.value,
            ended_at=ended_at,
            end_latitude=end_latitude,
            end_longitude=end_longitude,
        )

        action_map = {
            ShiftStatus.PAUSED: codes.ACTION_SHIFT_PAUSED,
            ShiftStatus.STARTED: codes.ACTION_SHIFT_RESUMED,
            ShiftStatus.ENDED: codes.ACTION_SHIFT_ENDED,
        }
        await record_audit(
            actor=principal,
            action=action_map.get(to_status, codes.ACTION_SHIFT_STARTED),
            resource_type="rider_shift",
            resource_id=shift.id,
            metadata={"to": to_status.value, "from": shift.status},
        )
        return await self.shifts.get(shift_id)

    # ==================================================================
    # LOCATION INGEST
    # ==================================================================
    async def ingest_location(
        self, *,
        rider_id: UUID,
        latitude: Decimal,
        longitude: Decimal,
        recorded_at: datetime,
        accuracy_meters: Decimal | None = None,
        speed_kmh: Decimal | None = None,
        heading_degrees: Decimal | None = None,
        battery_level: int | None = None,
        is_mocked: bool = False,
        principal: Principal | SystemPrincipal | None = None,
        stale_seconds: int = 90,
        reject_mocked: bool = True,
    ):
        """Validate freshness + write a ping. Returns the new row.

        ``stale_seconds=90`` means we accept pings from up to 15min in
        the past (10× to forgive offline buffering); anything older
        gets rejected so the rider's app re-tries with a fresh ping.
        """
        if reject_mocked and is_mocked:
            raise MockedLocationRejectedError(
                "Mocked location pings are rejected by policy.",
            )

        now = utc_now()
        # Cap how far in the past we'll accept. The pack uses 10x the
        # stale window — gives the app some catch-up room after offline.
        max_lag = timedelta(seconds=stale_seconds * 10)
        if recorded_at < now - max_lag:
            raise StaleLocationError(
                f"Ping is older than {stale_seconds * 10}s; please refresh GPS.",
            )

        # Find the open shift for this rider; allowed only if STARTED
        # or PAUSED.
        shift = await self.shifts.get_open_for_rider(rider_id)
        if shift is None:
            raise ShiftClosedError(
                "Rider has no open shift. Start a shift before sending pings.",
            )
        if shift.status not in {s.value for s in SHIFT_LOCATION_OK}:
            raise ShiftClosedError(
                f"Shift status '{shift.status}' does not accept location pings.",
            )

        row = await self.locations.add(
            rider_id=rider_id,
            shift_id=shift.id,
            latitude=latitude,
            longitude=longitude,
            accuracy_meters=accuracy_meters,
            speed_kmh=speed_kmh,
            heading_degrees=heading_degrees,
            battery_level=battery_level,
            is_mocked=is_mocked,
            recorded_at=recorded_at,
        )
        # Audit only every 10th ping to avoid drowning the log — the
        # raw stream is in rider_live_locations already.
        # (Cheap heuristic via id hex.)
        if row.id.hex.endswith("0"):
            await record_audit(
                actor=principal,
                action=codes.ACTION_LOCATION_INGESTED,
                resource_type="rider_shift",
                resource_id=shift.id,
                metadata={
                    "rider_id": str(rider_id),
                    "lat": float(latitude),
                    "lng": float(longitude),
                },
            )
        return row

    # ==================================================================
    # NEXT STOP RECOMMENDATION
    # ==================================================================
    async def recommend_next_stop(
        self, *,
        run_sheet_id: UUID,
        principal: Principal | SystemPrincipal,
        trigger: RecalcTrigger = RecalcTrigger.MANUAL_REFRESH,
        eta_improvement_threshold_min: int = 5,
    ) -> tuple[RecommendationResult, bool]:
        """Compute the next stop. Returns (result, changed).

        Even if the proposed stop equals the current next stop, we
        write a recalc log row (with reason="same_stop") so audit can
        see the engine *did* run.

        ``changed=True`` only when the gating logic decided to actually
        flip; the API layer surfaces this so the rider app can choose
        whether to ping the rider with a "stop changed" notification.
        """
        run_sheet = await self.run_sheets.get(run_sheet_id)
        if run_sheet is None:
            raise RunSheetNotFoundError("Run sheet not found.")

        candidates = list(await self.stops.list_candidates(run_sheet_id))
        current_next = await self.stops.get_current_next(run_sheet_id)

        # If the sheet is frozen, return whatever the current next is
        # without any recompute. We still log so audit shows the freeze
        # blocked the recompute.
        if run_sheet.sequence_frozen:
            await self.recalc_logs.add(
                run_sheet_id=run_sheet_id,
                rider_id=run_sheet.rider_id,
                trigger_type=trigger.value,
                old_next_stop_id=current_next.id if current_next else None,
                new_next_stop_id=current_next.id if current_next else None,
                candidates_considered=len(candidates),
                reason="sheet_frozen",
            )
            from app.modules.rider_routing.next_stop import RecommendationResult
            return RecommendationResult(
                chosen=None, alternates=[], candidates_considered=len(candidates),
            ), False

        if not candidates and current_next is None:
            raise NoCandidateStopsError(
                "No eligible stops to recommend right now.",
            )

        # Use the rider's last known location to score by haversine ETA.
        last_loc = await self.locations.latest_for_rider(run_sheet.rider_id)
        rider_lat = float(last_loc.latitude) if last_loc else None
        rider_lng = float(last_loc.longitude) if last_loc else None
        # Same-zone bonus: use the most-recently delivered stop's zone
        # as the rider's "current zone".
        recent_delivered = await self.stops.list_for_sheet(
            run_sheet_id, statuses=[StopStatus.DELIVERED.value], order="dynamic",
        )
        current_zone = (
            recent_delivered[-1].delivery_zone_code if recent_delivered else None
        )

        result = next_stop.score_candidates(
            candidates=candidates,
            rider_lat=rider_lat,
            rider_lng=rider_lng,
            current_zone_code=current_zone,
            weights=DEFAULT_WEIGHTS,
        )

        should_change, reason = next_stop.should_change_next_stop(
            current=current_next,
            proposed=result.chosen,
            eta_improvement_threshold_min=eta_improvement_threshold_min,
        )

        # Persist recommendation_score on every candidate that was
        # scored (including non-chosen) so the admin live-map can
        # render the breakdown.
        for bd in [*([result.chosen] if result.chosen else []),
                   *result.alternates]:
            await self.stops.update(
                stop_id=bd.stop_id,
                recommendation_score=Decimal(str(bd.score)),
                last_recomputed_at=utc_now(),
                estimated_travel_time_min=bd.eta_minutes,
                estimated_distance_km=Decimal(str(round(bd.distance_km, 2)))
                if bd.distance_km is not None else None,
            )

        new_id = result.chosen.stop_id if (
            should_change and result.chosen is not None
        ) else (current_next.id if current_next else None)
        await self.recalc_logs.add(
            run_sheet_id=run_sheet_id,
            rider_id=run_sheet.rider_id,
            trigger_type=trigger.value,
            old_next_stop_id=current_next.id if current_next else None,
            new_next_stop_id=new_id,
            candidates_considered=len(candidates),
            reason=reason,
            payload_json={
                "chosen_score": (
                    result.chosen.score if result.chosen else None
                ),
                "chosen_reasons": (
                    result.chosen.reasons if result.chosen else []
                ),
            },
        )

        if should_change and result.chosen is not None:
            await record_audit(
                actor=principal,
                action=codes.ACTION_NEXT_STOP_CHANGED,
                resource_type="run_sheet",
                resource_id=run_sheet_id,
                metadata={
                    "old_stop_id": (
                        str(current_next.id) if current_next else None
                    ),
                    "new_stop_id": str(result.chosen.stop_id),
                    "trigger": trigger.value,
                },
            )

        return result, should_change

    # ==================================================================
    # STOP LIFECYCLE
    # ==================================================================
    async def start_stop(
        self, *,
        stop_id: UUID,
        rider_id: UUID,
        principal: Principal | SystemPrincipal,
    ):
        return await self._transition_stop(
            stop_id=stop_id,
            rider_id=rider_id,
            principal=principal,
            to_status=StopStatus.ENROUTE,
            audit_action=codes.ACTION_STOP_STARTED,
        )

    async def arrive_stop(
        self, *,
        stop_id: UUID,
        rider_id: UUID,
        principal: Principal | SystemPrincipal,
    ):
        return await self._transition_stop(
            stop_id=stop_id,
            rider_id=rider_id,
            principal=principal,
            to_status=StopStatus.ARRIVED,
            audit_action=codes.ACTION_STOP_ARRIVED,
        )

    async def complete_stop(
        self, *,
        stop_id: UUID,
        rider_id: UUID,
        principal: Principal | SystemPrincipal,
        cash_collected_bdt: Decimal | None = None,
    ):
        """Mark stop delivered + bump attempt count + update sheet counts.

        The actual POD/COD is recorded against the underlying
        ``delivery_assignment`` via the deliveries module. This method
        only flips the *stop* status — call deliveries.service first
        for POD upload, then this for sequencing.
        """
        stop = await self._transition_stop(
            stop_id=stop_id,
            rider_id=rider_id,
            principal=principal,
            to_status=StopStatus.DELIVERED,
            audit_action=codes.ACTION_STOP_DELIVERED,
            extra_metadata={
                "cash_collected_bdt": (
                    float(cash_collected_bdt) if cash_collected_bdt else 0.0
                ),
            },
        )
        await self.stops.update(
            stop_id=stop_id,
            attempt_count=(stop.attempt_count or 0) + 1,
        )
        await self.run_sheets.recompute_counts(stop.run_sheet_id)
        # If this was the last stop, mark the sheet completed.
        await self._maybe_close_sheet(stop.run_sheet_id)
        # Trigger a recompute now that one stop is done (rider needs
        # to know the next one).
        try:
            await self.recommend_next_stop(
                run_sheet_id=stop.run_sheet_id,
                principal=principal,
                trigger=RecalcTrigger.STOP_COMPLETED,
            )
        except NoCandidateStopsError:
            pass  # Sheet is fully done — handled by _maybe_close_sheet
        return stop

    async def fail_stop(
        self, *,
        stop_id: UUID,
        rider_id: UUID,
        principal: Principal | SystemPrincipal,
        reason: str,
    ):
        stop = await self._transition_stop(
            stop_id=stop_id,
            rider_id=rider_id,
            principal=principal,
            to_status=StopStatus.FAILED,
            audit_action=codes.ACTION_STOP_FAILED,
            extra_metadata={"reason": reason},
        )
        await self.stops.update(
            stop_id=stop_id,
            attempt_count=(stop.attempt_count or 0) + 1,
        )
        await self.run_sheets.recompute_counts(stop.run_sheet_id)
        await self._maybe_close_sheet(stop.run_sheet_id)
        try:
            await self.recommend_next_stop(
                run_sheet_id=stop.run_sheet_id,
                principal=principal,
                trigger=RecalcTrigger.STOP_FAILED,
            )
        except NoCandidateStopsError:
            pass
        return stop

    async def reschedule_stop(
        self, *,
        stop_id: UUID,
        rider_id: UUID,
        principal: Principal | SystemPrincipal,
        promised_start_at: datetime | None = None,
        promised_end_at: datetime | None = None,
    ):
        """Push the stop back to ASSIGNED with new promised window."""
        stop = await self.stops.get(stop_id)
        if stop is None:
            raise StopNotFoundError("Stop not found.")
        await self._assert_stop_belongs_to_rider(stop, rider_id)
        # We allow reschedule from enroute/arrived back to assigned;
        # use the same transition guard for safety.
        if stop.stop_status not in (
            StopStatus.ENROUTE.value,
            StopStatus.ARRIVED.value,
            StopStatus.READY.value,
        ):
            raise IllegalStopTransitionError(
                f"Cannot reschedule from status '{stop.stop_status}'.",
            )
        await self.stops.update(
            stop_id=stop_id,
            stop_status=StopStatus.ASSIGNED.value,
            promised_start_at=promised_start_at,
            promised_end_at=promised_end_at,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_STOP_RESCHEDULED,
            resource_type="run_sheet_stop",
            resource_id=stop_id,
            metadata={
                "rider_id": str(rider_id),
                "new_promised_end": (
                    promised_end_at.isoformat() if promised_end_at else None
                ),
            },
        )
        return await self.stops.get(stop_id)

    # ==================================================================
    # Helpers
    # ==================================================================
    async def _transition_stop(
        self, *,
        stop_id: UUID,
        rider_id: UUID,
        principal: Principal | SystemPrincipal,
        to_status: StopStatus,
        audit_action: str,
        extra_metadata: dict[str, Any] | None = None,
    ):
        stop = await self.stops.get(stop_id)
        if stop is None:
            raise StopNotFoundError("Stop not found.")
        await self._assert_stop_belongs_to_rider(stop, rider_id)

        legal = STOP_TRANSITIONS.get(StopStatus(stop.stop_status), frozenset())
        if to_status not in legal:
            raise IllegalStopTransitionError(
                f"Stop transition not allowed: {stop.stop_status} → "
                f"{to_status.value}",
            )

        await self.stops.update(stop_id=stop_id, stop_status=to_status.value)
        # Run sheet flips DRAFT/ASSIGNED → IN_PROGRESS on first non-
        # terminal stop transition.
        await self._maybe_mark_sheet_in_progress(stop.run_sheet_id)
        meta: dict[str, Any] = {
            "rider_id": str(rider_id),
            "from": stop.stop_status,
            "to": to_status.value,
        }
        if extra_metadata:
            meta.update(extra_metadata)
        await record_audit(
            actor=principal,
            action=audit_action,
            resource_type="run_sheet_stop",
            resource_id=stop_id,
            metadata=meta,
        )
        return await self.stops.get(stop_id)

    async def _assert_stop_belongs_to_rider(
        self, stop, rider_id: UUID,
    ) -> None:
        run_sheet = await self.run_sheets.get(stop.run_sheet_id)
        if run_sheet is None:
            raise RunSheetNotFoundError("Parent run sheet not found.")
        if run_sheet.rider_id != rider_id:
            raise StopNotForRiderError(
                "Stop belongs to another rider's run sheet.",
            )

    async def _assert_rider_exists(self, rider_id: UUID) -> None:
        rider = await self.session.get(Rider, rider_id)
        if rider is None or not rider.is_active:
            raise RiderNotFoundError("Rider not found or inactive.")

    async def _maybe_mark_sheet_in_progress(self, run_sheet_id: UUID) -> None:
        sheet = await self.run_sheets.get(run_sheet_id)
        if sheet is None:
            return
        if sheet.status == RunSheetStatus.ASSIGNED.value:
            await self.run_sheets.update(
                run_sheet_id=run_sheet_id,
                status=RunSheetStatus.IN_PROGRESS.value,
            )

    async def _maybe_close_sheet(self, run_sheet_id: UUID) -> None:
        """If every stop is in a terminal state, mark the sheet completed."""
        remaining = await self.stops.list_for_sheet(
            run_sheet_id,
            statuses=[
                StopStatus.ASSIGNED.value,
                StopStatus.READY.value,
                StopStatus.ENROUTE.value,
                StopStatus.ARRIVED.value,
            ],
        )
        if not remaining:
            await self.run_sheets.update(
                run_sheet_id=run_sheet_id,
                status=RunSheetStatus.COMPLETED.value,
            )
