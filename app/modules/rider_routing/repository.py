"""SQLAlchemy wrappers for rider_routing tables.

Each repo is intentionally thin — single statements per method, no
business logic. The service layer handles transitions, audit, and
cross-row updates.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rider_routing.models import (
    OpsRouteOverride,
    RiderLiveLocation,
    RiderShift,
    RouteEtaSnapshot,
    RouteRecalculationLog,
    RunSheet,
    RunSheetStop,
)
from app.modules.rider_routing.state import (
    SHIFT_LOCATION_OK,
    STOP_CANDIDATE_STATUSES,
    STOP_TERMINAL_STATUSES,
    RunSheetStatus,
    ShiftStatus,
    StopStatus,
)


# ============================================================
#  RiderShiftRepository
# ============================================================
class RiderShiftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, shift_id: UUID) -> RiderShift | None:
        return await self.session.get(RiderShift, shift_id)

    async def get_open_for_rider(self, rider_id: UUID) -> RiderShift | None:
        """Return the rider's currently STARTED or PAUSED shift, if any."""
        return (
            await self.session.execute(
                select(RiderShift)
                .where(
                    and_(
                        RiderShift.rider_id == rider_id,
                        RiderShift.status.in_(
                            [s.value for s in SHIFT_LOCATION_OK],
                        ),
                    ),
                )
                .order_by(desc(RiderShift.started_at))
                .limit(1),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> RiderShift:
        row = RiderShift(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_status(
        self, *,
        shift_id: UUID,
        status: str,
        ended_at: datetime | None = None,
        end_latitude: Decimal | None = None,
        end_longitude: Decimal | None = None,
    ) -> int:
        values: dict[str, Any] = {"status": status}
        if ended_at is not None:
            values["ended_at"] = ended_at
        if end_latitude is not None:
            values["end_latitude"] = end_latitude
        if end_longitude is not None:
            values["end_longitude"] = end_longitude
        result = await self.session.execute(
            update(RiderShift)
            .where(RiderShift.id == shift_id)
            .values(**values),
        )
        return int(result.rowcount or 0)


# ============================================================
#  RiderLiveLocationRepository
# ============================================================
class RiderLiveLocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> RiderLiveLocation:
        row = RiderLiveLocation(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def latest_for_rider(self, rider_id: UUID) -> RiderLiveLocation | None:
        return (
            await self.session.execute(
                select(RiderLiveLocation)
                .where(RiderLiveLocation.rider_id == rider_id)
                .order_by(desc(RiderLiveLocation.recorded_at))
                .limit(1),
            )
        ).scalar_one_or_none()

    async def recent_for_rider(
        self, rider_id: UUID, *, limit: int = 50,
    ) -> Sequence[RiderLiveLocation]:
        return (
            (
                await self.session.execute(
                    select(RiderLiveLocation)
                    .where(RiderLiveLocation.rider_id == rider_id)
                    .order_by(desc(RiderLiveLocation.recorded_at))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def list_active_riders_with_recent_pings(
        self, *,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """For the live-map endpoint: each rider's latest known position
        as of ``since``. Done as one round-trip via a window function.
        """
        from sqlalchemy import func, literal_column

        # Use a CTE that ranks pings within (rider) by recorded_at desc
        # and picks rank=1.
        ranked = (
            select(
                RiderLiveLocation.rider_id.label("rider_id"),
                RiderLiveLocation.latitude.label("latitude"),
                RiderLiveLocation.longitude.label("longitude"),
                RiderLiveLocation.speed_kmh.label("speed_kmh"),
                RiderLiveLocation.heading_degrees.label("heading_degrees"),
                RiderLiveLocation.battery_level.label("battery_level"),
                RiderLiveLocation.recorded_at.label("recorded_at"),
                func.row_number().over(
                    partition_by=RiderLiveLocation.rider_id,
                    order_by=desc(RiderLiveLocation.recorded_at),
                ).label("rn"),
            )
            .where(RiderLiveLocation.recorded_at >= since)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(ranked).where(literal_column("rn") == 1),
            )
        ).all()
        return [
            {
                "rider_id": r.rider_id,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "speed_kmh": r.speed_kmh,
                "heading_degrees": r.heading_degrees,
                "battery_level": r.battery_level,
                "recorded_at": r.recorded_at,
            }
            for r in rows
        ]


# ============================================================
#  RunSheetRepository
# ============================================================
class RunSheetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, run_sheet_id: UUID) -> RunSheet | None:
        return await self.session.get(RunSheet, run_sheet_id)

    async def get_active_for_rider(self, rider_id: UUID) -> RunSheet | None:
        """Latest non-terminal sheet for the rider — there should be at
        most one active sheet (assigned/in_progress) per rider.
        """
        return (
            await self.session.execute(
                select(RunSheet)
                .where(
                    and_(
                        RunSheet.rider_id == rider_id,
                        RunSheet.status.in_([
                            RunSheetStatus.ASSIGNED.value,
                            RunSheetStatus.IN_PROGRESS.value,
                        ]),
                    ),
                )
                .order_by(desc(RunSheet.sheet_date), desc(RunSheet.created_at))
                .limit(1),
            )
        ).scalar_one_or_none()

    async def list_recent(
        self, *, limit: int = 100,
    ) -> Sequence[RunSheet]:
        return (
            (
                await self.session.execute(
                    select(RunSheet)
                    .order_by(desc(RunSheet.sheet_date), desc(RunSheet.id))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def add(self, **fields: Any) -> RunSheet:
        row = RunSheet(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(
        self, *,
        run_sheet_id: UUID,
        **fields: Any,
    ) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(RunSheet)
            .where(RunSheet.id == run_sheet_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)

    async def recompute_counts(self, run_sheet_id: UUID) -> None:
        """Refresh the cached counts on the run sheet from its stops.

        Called after every stop transition. Cheap (one row + COUNT
        FILTER) and avoids drift if a stop write fails midway.
        """
        from sqlalchemy import func

        agg = (
            await self.session.execute(
                select(
                    func.count(RunSheetStop.id).label("planned"),
                    func.count(RunSheetStop.id).filter(
                        RunSheetStop.stop_status == StopStatus.DELIVERED.value,
                    ).label("completed"),
                    func.count(RunSheetStop.id).filter(
                        RunSheetStop.stop_status == StopStatus.FAILED.value,
                    ).label("failed"),
                    func.coalesce(
                        func.sum(RunSheetStop.cash_to_collect_bdt), 0,
                    ).label("total_cod"),
                )
                .where(RunSheetStop.run_sheet_id == run_sheet_id),
            )
        ).one()
        await self.session.execute(
            update(RunSheet)
            .where(RunSheet.id == run_sheet_id)
            .values(
                planned_stop_count=int(agg.planned or 0),
                completed_stop_count=int(agg.completed or 0),
                failed_stop_count=int(agg.failed or 0),
                total_cod_amount=Decimal(agg.total_cod or 0),
            ),
        )


# ============================================================
#  RunSheetStopRepository
# ============================================================
class RunSheetStopRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, stop_id: UUID) -> RunSheetStop | None:
        return await self.session.get(RunSheetStop, stop_id)

    async def list_for_sheet(
        self,
        run_sheet_id: UUID,
        *,
        statuses: Sequence[str] | None = None,
        order: str = "dynamic",
    ) -> Sequence[RunSheetStop]:
        stmt = (
            select(RunSheetStop)
            .where(RunSheetStop.run_sheet_id == run_sheet_id)
        )
        if statuses:
            stmt = stmt.where(RunSheetStop.stop_status.in_(list(statuses)))
        if order == "dynamic":
            # NULLs last — use coalesce so dynamic_sequence not yet set
            # ranks AFTER assigned ones.
            from sqlalchemy import case
            stmt = stmt.order_by(
                case(
                    (RunSheetStop.dynamic_sequence.is_(None), 1),
                    else_=0,
                ),
                RunSheetStop.dynamic_sequence.asc(),
                RunSheetStop.planned_sequence.asc(),
            )
        else:
            stmt = stmt.order_by(RunSheetStop.planned_sequence.asc())
        return (await self.session.execute(stmt)).scalars().all()

    async def list_candidates(
        self, run_sheet_id: UUID,
    ) -> Sequence[RunSheetStop]:
        """Stops eligible for next-stop recommendation."""
        return (
            (
                await self.session.execute(
                    select(RunSheetStop)
                    .where(
                        and_(
                            RunSheetStop.run_sheet_id == run_sheet_id,
                            RunSheetStop.stop_status.in_(
                                [s.value for s in STOP_CANDIDATE_STATUSES],
                            ),
                        ),
                    ),
                )
            )
            .scalars()
            .all()
        )

    async def get_current_next(
        self, run_sheet_id: UUID,
    ) -> RunSheetStop | None:
        """The stop the rider is currently committed to (enroute or
        arrived). Returns at most one — there can only be one such stop
        per run sheet at a time.
        """
        return (
            await self.session.execute(
                select(RunSheetStop)
                .where(
                    and_(
                        RunSheetStop.run_sheet_id == run_sheet_id,
                        RunSheetStop.stop_status.in_([
                            StopStatus.ENROUTE.value,
                            StopStatus.ARRIVED.value,
                        ]),
                    ),
                )
                .limit(1),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> RunSheetStop:
        row = RunSheetStop(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, *, stop_id: UUID, **fields: Any) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(RunSheetStop)
            .where(RunSheetStop.id == stop_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)

    async def bulk_update_dynamic_sequence(
        self,
        run_sheet_id: UUID,
        ordered_stop_ids: Sequence[UUID],
    ) -> None:
        """Re-number dynamic_sequence (1..N) for the supplied stops.

        Stops not in the list get NULL'd so the ordering is unambiguous.
        """
        await self.session.execute(
            update(RunSheetStop)
            .where(RunSheetStop.run_sheet_id == run_sheet_id)
            .values(dynamic_sequence=None),
        )
        for i, sid in enumerate(ordered_stop_ids, start=1):
            await self.session.execute(
                update(RunSheetStop)
                .where(RunSheetStop.id == sid)
                .values(dynamic_sequence=i),
            )


# ============================================================
#  RouteRecalculationLogRepository
# ============================================================
class RouteRecalculationLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> RouteRecalculationLog:
        row = RouteRecalculationLog(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_sheet(
        self,
        run_sheet_id: UUID,
        *,
        limit: int = 100,
    ) -> Sequence[RouteRecalculationLog]:
        return (
            (
                await self.session.execute(
                    select(RouteRecalculationLog)
                    .where(
                        RouteRecalculationLog.run_sheet_id == run_sheet_id,
                    )
                    .order_by(desc(RouteRecalculationLog.created_at))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )


# ============================================================
#  RouteEtaSnapshotRepository
# ============================================================
class RouteEtaSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> RouteEtaSnapshot:
        row = RouteEtaSnapshot(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_stop(
        self,
        stop_id: UUID,
        *,
        limit: int = 50,
    ) -> Sequence[RouteEtaSnapshot]:
        return (
            (
                await self.session.execute(
                    select(RouteEtaSnapshot)
                    .where(RouteEtaSnapshot.run_sheet_stop_id == stop_id)
                    .order_by(desc(RouteEtaSnapshot.snapshot_at))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )


# ============================================================
#  OpsRouteOverrideRepository
# ============================================================
class OpsRouteOverrideRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> OpsRouteOverride:
        row = OpsRouteOverride(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_sheet(
        self,
        run_sheet_id: UUID,
        *,
        limit: int = 100,
    ) -> Sequence[OpsRouteOverride]:
        return (
            (
                await self.session.execute(
                    select(OpsRouteOverride)
                    .where(OpsRouteOverride.run_sheet_id == run_sheet_id)
                    .order_by(desc(OpsRouteOverride.created_at))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )
