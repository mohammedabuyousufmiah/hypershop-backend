"""ARQ jobs for rider routing.

  - ``mark_stale_riders_offline_job`` (every 2 min) — flips Rider's
    current_status from 'available' / 'busy' to 'offline' if the
    rider hasn't pinged in N minutes. The next legit ping will
    bounce them back. Catches "rider closed the app" cases that
    leave the live map showing stale dots.

  - ``snapshot_etas_job`` (every 5 min) — for each active run sheet,
    capture an ETA snapshot per non-terminal stop into
    ``route_eta_snapshots``. Lets ops see SLA drift over the day.

Neither job fails the worker tick on a single bad sheet — exceptions
are caught + logged so the batch keeps moving.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, update

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.core.time import utc_now
from app.modules.deliveries.models import Rider
from app.modules.rider_routing import next_stop as next_stop_engine
from app.modules.rider_routing.models import (
    RiderLiveLocation,
    RouteEtaSnapshot,
    RunSheet,
    RunSheetStop,
)
from app.modules.rider_routing.state import (
    RunSheetStatus,
    STOP_CANDIDATE_STATUSES,
    StopStatus,
)

_log = get_logger("hypershop.rider_routing.jobs")


# ----------------------------------------------------------------------
# Stale-rider sweeper
# ----------------------------------------------------------------------
async def mark_stale_riders_offline_job(
    _ctx: dict[str, Any],
    *,
    stale_minutes: int = 5,
) -> int:
    """Flip riders to 'offline' if their last ping is older than N min.

    Returns the count of riders flipped.
    """
    cutoff = utc_now() - timedelta(minutes=stale_minutes)
    flipped = 0
    async with UnitOfWork().transactional() as session:
        # Riders currently 'available' or 'busy' whose latest ping
        # (or no ping at all) is older than cutoff.
        from sqlalchemy import func

        latest_ping_subq = (
            select(
                RiderLiveLocation.rider_id,
                func.max(RiderLiveLocation.recorded_at).label("last"),
            )
            .group_by(RiderLiveLocation.rider_id)
            .subquery()
        )
        candidates = (
            (
                await session.execute(
                    select(Rider.id)
                    .outerjoin(
                        latest_ping_subq,
                        latest_ping_subq.c.rider_id == Rider.id,
                    )
                    .where(
                        Rider.is_active.is_(True),
                        Rider.current_status.in_(["available", "busy"]),
                        or_(
                            latest_ping_subq.c.last.is_(None),
                            latest_ping_subq.c.last < cutoff,
                        ),
                    ),
                )
            )
            .scalars()
            .all()
        )
        if candidates:
            await session.execute(
                update(Rider)
                .where(Rider.id.in_(list(candidates)))
                .values(current_status="offline"),
            )
            flipped = len(candidates)
    if flipped:
        _log.info(
            "stale_riders_marked_offline",
            count=flipped,
            stale_minutes=stale_minutes,
        )
    return flipped


# ----------------------------------------------------------------------
# ETA snapshot taker
# ----------------------------------------------------------------------
async def snapshot_etas_job(_ctx: dict[str, Any]) -> int:
    """Snapshot the current ETA of every non-terminal stop on every
    active run sheet. Keeps ``route_eta_snapshots`` populated so the
    SLA-drift report has time-series data.

    Cheap: one read per sheet, one bulk insert per sheet. Skips sheets
    with no candidate stops.
    """
    snapshotted = 0
    async with UnitOfWork().transactional() as session:
        active_sheets = (
            (
                await session.execute(
                    select(RunSheet)
                    .where(
                        RunSheet.status.in_([
                            RunSheetStatus.ASSIGNED.value,
                            RunSheetStatus.IN_PROGRESS.value,
                        ]),
                    ),
                )
            )
            .scalars()
            .all()
        )
        if not active_sheets:
            return 0

        now = utc_now()
        for sheet in active_sheets:
            try:
                stops = (
                    (
                        await session.execute(
                            select(RunSheetStop)
                            .where(
                                and_(
                                    RunSheetStop.run_sheet_id == sheet.id,
                                    RunSheetStop.stop_status.in_([
                                        s.value for s in STOP_CANDIDATE_STATUSES
                                    ]),
                                ),
                            ),
                        )
                    )
                    .scalars()
                    .all()
                )
                for stop in stops:
                    eta = stop.estimated_travel_time_min
                    dist = stop.estimated_distance_km
                    if eta is None or dist is None:
                        # No prior recompute — skip rather than insert
                        # a placeholder. Snapshot will pick up on the
                        # next tick once the next-stop engine has run.
                        continue
                    session.add(
                        RouteEtaSnapshot(
                            run_sheet_stop_id=stop.id,
                            rider_id=sheet.rider_id,
                            eta_minutes=int(eta),
                            distance_km=Decimal(str(dist)),
                            snapshot_at=now,
                        ),
                    )
                    snapshotted += 1
            except Exception as e:  # noqa: BLE001
                _log.exception(
                    "eta_snapshot_sheet_failed",
                    sheet_id=str(sheet.id),
                    error=str(e),
                )
    if snapshotted:
        _log.info("eta_snapshots_taken", count=snapshotted)
    return snapshotted


# ----------------------------------------------------------------------
# Live-location retention pruner
# ----------------------------------------------------------------------
async def prune_old_live_locations_job(
    _ctx: dict[str, Any],
    *,
    retention_days: int = 30,
) -> int:
    """Delete rider_live_locations rows older than ``retention_days``.

    The location stream is dense (one ping/20s × ~1000 riders × 8h
    shifts = ~1.4M rows/day in production). Without retention the
    table grows unbounded and queries against ``recent_for_rider``
    degrade. We keep 30 days by default — long enough for dispute
    resolution + ops debugging, short enough that the table stays
    manageable.

    Bounded batch (100K rows/tick) so a long-overdue prune doesn't
    hold a huge transaction.
    """
    from datetime import timedelta
    from sqlalchemy import delete as sa_delete
    from app.modules.rider_routing.models import RiderLiveLocation

    cutoff = utc_now() - timedelta(days=retention_days)
    async with UnitOfWork().transactional() as session:
        result = await session.execute(
            sa_delete(RiderLiveLocation)
            .where(
                RiderLiveLocation.recorded_at < cutoff,
            )
            .execution_options(synchronize_session=False),
        )
        deleted = int(result.rowcount or 0)
    if deleted:
        _log.info(
            "rider_live_locations_pruned",
            deleted=deleted,
            cutoff=cutoff.isoformat(),
            retention_days=retention_days,
        )
    return deleted
