"""Dispatcher-side override actions.

Kept separate from RiderRoutingService so the rider-app code path is
crystal clear about which actions are *rider-driven* vs *ops-driven*.

Each override writes a row in ``ops_route_overrides`` AND audit_log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import ValidationError
from app.core.security.principal import Principal
from app.modules.rider_routing import codes, state
from app.modules.rider_routing.errors import (
    RunSheetNotFoundError,
    StopNotFoundError,
)
from app.modules.rider_routing.repository import (
    OpsRouteOverrideRepository,
    RunSheetRepository,
    RunSheetStopRepository,
)
from app.modules.rider_routing.state import (
    OverrideType,
    RunSheetStatus,
    StopStatus,
)


class OpsRouteOverrideService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.overrides = OpsRouteOverrideRepository(session)
        self.run_sheets = RunSheetRepository(session)
        self.stops = RunSheetStopRepository(session)

    # ------------------------------------------------------------------
    # Force next stop
    # ------------------------------------------------------------------
    async def force_next_stop(
        self, *,
        run_sheet_id: UUID,
        target_stop_id: UUID,
        reason: str,
        principal: Principal,
        expires_at: datetime | None = None,
    ):
        sheet = await self.run_sheets.get(run_sheet_id)
        if sheet is None:
            raise RunSheetNotFoundError("Run sheet not found.")
        target = await self.stops.get(target_stop_id)
        if target is None or target.run_sheet_id != run_sheet_id:
            raise StopNotFoundError(
                "Target stop not found in this run sheet.",
            )
        if target.stop_status not in (
            StopStatus.ASSIGNED.value,
            StopStatus.READY.value,
            StopStatus.ENROUTE.value,
        ):
            raise ValidationError(
                f"Target stop status '{target.stop_status}' is not "
                f"forceable.",
            )

        # Pin dynamic_sequence=1 so the rider's app shows it next.
        # Other stops keep their order; we DON'T re-number them all.
        await self.stops.update(
            stop_id=target_stop_id,
            dynamic_sequence=1,
        )
        row = await self.overrides.add(
            run_sheet_id=run_sheet_id,
            run_sheet_stop_id=target_stop_id,
            override_type=OverrideType.FORCE_NEXT_STOP.value,
            reason=reason,
            created_by=principal.user_id,
            expires_at=expires_at,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_OVERRIDE_FORCE_NEXT,
            resource_type="run_sheet",
            resource_id=run_sheet_id,
            metadata={
                "stop_id": str(target_stop_id),
                "reason": reason,
                "override_id": str(row.id),
            },
        )
        return row

    # ------------------------------------------------------------------
    # Freeze / unfreeze sequence
    # ------------------------------------------------------------------
    async def freeze_sequence(
        self, *,
        run_sheet_id: UUID,
        reason: str,
        principal: Principal,
        expires_at: datetime | None = None,
    ):
        sheet = await self.run_sheets.get(run_sheet_id)
        if sheet is None:
            raise RunSheetNotFoundError("Run sheet not found.")
        await self.run_sheets.update(
            run_sheet_id=run_sheet_id, sequence_frozen=True,
        )
        row = await self.overrides.add(
            run_sheet_id=run_sheet_id,
            override_type=OverrideType.FREEZE_SEQUENCE.value,
            reason=reason,
            created_by=principal.user_id,
            expires_at=expires_at,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_OVERRIDE_FREEZE,
            resource_type="run_sheet",
            resource_id=run_sheet_id,
            metadata={"reason": reason, "override_id": str(row.id)},
        )
        return row

    async def unfreeze_sequence(
        self, *,
        run_sheet_id: UUID,
        reason: str,
        principal: Principal,
    ):
        sheet = await self.run_sheets.get(run_sheet_id)
        if sheet is None:
            raise RunSheetNotFoundError("Run sheet not found.")
        await self.run_sheets.update(
            run_sheet_id=run_sheet_id, sequence_frozen=False,
        )
        row = await self.overrides.add(
            run_sheet_id=run_sheet_id,
            override_type=OverrideType.UNFREEZE.value,
            reason=reason,
            created_by=principal.user_id,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_OVERRIDE_UNFREEZE,
            resource_type="run_sheet",
            resource_id=run_sheet_id,
            metadata={"reason": reason, "override_id": str(row.id)},
        )
        return row

    # ------------------------------------------------------------------
    # Skip stop
    # ------------------------------------------------------------------
    async def skip_stop(
        self, *,
        stop_id: UUID,
        reason: str,
        principal: Principal,
    ):
        stop = await self.stops.get(stop_id)
        if stop is None:
            raise StopNotFoundError("Stop not found.")
        if stop.stop_status not in (
            StopStatus.ASSIGNED.value,
            StopStatus.READY.value,
            StopStatus.ENROUTE.value,
        ):
            raise ValidationError(
                f"Cannot skip stop in status '{stop.stop_status}'.",
            )
        await self.stops.update(
            stop_id=stop_id,
            stop_status=StopStatus.SKIPPED.value,
        )
        await self.run_sheets.recompute_counts(stop.run_sheet_id)
        row = await self.overrides.add(
            run_sheet_id=stop.run_sheet_id,
            run_sheet_stop_id=stop_id,
            override_type=OverrideType.SKIP_STOP.value,
            reason=reason,
            created_by=principal.user_id,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_OVERRIDE_SKIP_STOP,
            resource_type="run_sheet_stop",
            resource_id=stop_id,
            metadata={"reason": reason, "override_id": str(row.id)},
        )
        return row

    # ------------------------------------------------------------------
    # Reassign (move stop to another rider's open run sheet)
    # ------------------------------------------------------------------
    async def reassign_stop(
        self, *,
        stop_id: UUID,
        target_run_sheet_id: UUID,
        reason: str,
        principal: Principal,
    ):
        stop = await self.stops.get(stop_id)
        if stop is None:
            raise StopNotFoundError("Stop not found.")
        target_sheet = await self.run_sheets.get(target_run_sheet_id)
        if target_sheet is None:
            raise RunSheetNotFoundError("Target run sheet not found.")
        if target_sheet.status not in (
            RunSheetStatus.ASSIGNED.value,
            RunSheetStatus.IN_PROGRESS.value,
            RunSheetStatus.DRAFT.value,
        ):
            raise ValidationError(
                f"Target sheet status '{target_sheet.status}' is not "
                f"open for new stops.",
            )
        if stop.stop_status not in (
            StopStatus.ASSIGNED.value,
            StopStatus.READY.value,
        ):
            raise ValidationError(
                f"Cannot reassign a stop in status '{stop.stop_status}'.",
            )
        old_sheet_id = stop.run_sheet_id
        await self.stops.update(
            stop_id=stop_id,
            run_sheet_id=target_run_sheet_id,
        )
        # Recompute counts on both sheets.
        await self.run_sheets.recompute_counts(old_sheet_id)
        await self.run_sheets.recompute_counts(target_run_sheet_id)
        row = await self.overrides.add(
            run_sheet_id=target_run_sheet_id,
            run_sheet_stop_id=stop_id,
            override_type=OverrideType.REASSIGN_STOP.value,
            reason=reason,
            created_by=principal.user_id,
            payload_json={
                "from_run_sheet_id": str(old_sheet_id),
                "to_run_sheet_id": str(target_run_sheet_id),
            },
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_OVERRIDE_REASSIGN,
            resource_type="run_sheet_stop",
            resource_id=stop_id,
            metadata={
                "from_run_sheet_id": str(old_sheet_id),
                "to_run_sheet_id": str(target_run_sheet_id),
                "reason": reason,
                "override_id": str(row.id),
            },
        )
        return row

    # ------------------------------------------------------------------
    # Build a run sheet (admin-only — wraps a list of delivery
    # assignments into a brand new sheet)
    # ------------------------------------------------------------------
    async def create_run_sheet(
        self, *,
        rider_id: UUID,
        sheet_date,
        delivery_assignment_ids: list[UUID],
        principal: Principal,
        notes: str | None = None,
    ):
        """Create a DRAFT run sheet bundling N delivery_assignments.

        The sheet starts in DRAFT — admin must call ``assign`` to lock
        it before the rider sees it. Stops are added with planned
        sequence = position in the supplied list.

        Module 32 — wallet eligibility gate. Blocks creation if the
        rider's wallet is locked (previous-day unpaid + no carry-
        forward). Admin can still unlock the wallet first or approve
        carry-forward, then re-call create.
        """
        from app.modules.deliveries.models import DeliveryAssignment
        from app.modules.rider_wallet.service import (
            AssignmentEligibilityService,
        )
        await AssignmentEligibilityService(
            self.session,
        ).assert_or_raise(rider_id=rider_id)

        sheet = await self.run_sheets.add(
            rider_id=rider_id,
            sheet_date=sheet_date,
            status=RunSheetStatus.DRAFT.value,
            created_by=principal.user_id,
            notes=notes,
        )
        for i, da_id in enumerate(delivery_assignment_ids, start=1):
            da = await self.session.get(DeliveryAssignment, da_id)
            if da is None:
                raise ValidationError(
                    f"Delivery assignment {da_id} not found.",
                )
            await self.stops.add(
                run_sheet_id=sheet.id,
                delivery_assignment_id=da_id,
                planned_sequence=i,
                stop_status=StopStatus.ASSIGNED.value,
                cash_to_collect_bdt=(
                    da.cod_expected if da.cod_expected else 0
                ),
            )
        await self.run_sheets.recompute_counts(sheet.id)
        await record_audit(
            actor=principal,
            action=codes.ACTION_RUN_SHEET_CREATED,
            resource_type="run_sheet",
            resource_id=sheet.id,
            metadata={
                "rider_id": str(rider_id),
                "stop_count": len(delivery_assignment_ids),
            },
        )
        return await self.run_sheets.get(sheet.id)

    async def assign_run_sheet(
        self, *,
        run_sheet_id: UUID,
        principal: Principal,
    ):
        sheet = await self.run_sheets.get(run_sheet_id)
        if sheet is None:
            raise RunSheetNotFoundError("Run sheet not found.")
        if sheet.status != RunSheetStatus.DRAFT.value:
            raise ValidationError(
                f"Only DRAFT sheets can be assigned. Current: {sheet.status}.",
            )
        await self.run_sheets.update(
            run_sheet_id=run_sheet_id,
            status=RunSheetStatus.ASSIGNED.value,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_RUN_SHEET_ASSIGNED,
            resource_type="run_sheet",
            resource_id=run_sheet_id,
        )
        return await self.run_sheets.get(run_sheet_id)

    async def cancel_run_sheet(
        self, *,
        run_sheet_id: UUID,
        reason: str,
        principal: Principal,
    ):
        sheet = await self.run_sheets.get(run_sheet_id)
        if sheet is None:
            raise RunSheetNotFoundError("Run sheet not found.")
        if sheet.status in (
            RunSheetStatus.COMPLETED.value,
            RunSheetStatus.CANCELLED.value,
        ):
            raise ValidationError(
                f"Cannot cancel sheet in status '{sheet.status}'.",
            )
        await self.run_sheets.update(
            run_sheet_id=run_sheet_id,
            status=RunSheetStatus.CANCELLED.value,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_RUN_SHEET_CANCELLED,
            resource_type="run_sheet",
            resource_id=run_sheet_id,
            metadata={"reason": reason},
        )
        return await self.run_sheets.get(run_sheet_id)
