"""Delivery operations service.

Lifecycle entry points
----------------------
- :meth:`create_rider`, :meth:`update_rider` — admin manages the roster.
- :meth:`assign` — admin attaches an order (must be ``out_for_delivery``)
  to a rider. Initial status ``assigned``. Pre-fills ``cod_expected`` from
  the order's ``grand_total`` when payment_method=cod, else 0.
- :meth:`pickup` — rider marks pickup. ``assigned → picked_up``.
- :meth:`upload_pod_photo` — rider uploads a POD image (multipart). Saves
  via :class:`PodStorage`. May be called multiple times (overwrites).
- :meth:`deliver` — rider records the handover. Validates POD evidence
  is present (photo OR signature OR otp_verified) AND, for COD orders,
  ``cod_collected`` is supplied. Auto-reconciles if collected matches
  expected within tolerance, else flags ``discrepancy``. Status becomes
  ``delivered``; if cod_status is final (n/a or reconciled) we
  immediately advance to ``completed``.
- :meth:`complete` — internal helper that finalises a delivery and
  inline-calls :class:`OrderService.complete` so the order transitions
  to COMPLETED, which emits ``orders.order.completed`` → inventory
  consume handler drains reserved stock. **This is where the
  "delivery → stock deduct" rule operates.**
- :meth:`reconcile_cod` — supervisor closes a discrepancy. If amount was
  short, supervisor records resolution; the assignment then completes.
- :meth:`cancel`, :meth:`fail` — terminal exits other than completed.

Hard-rule mappings
------------------
- "POD mandatory" — :meth:`deliver` raises ``BusinessRuleError`` unless
  one of (photo, signature, otp) is present.
- "COD reconciliation" — :meth:`deliver` requires ``cod_collected`` for
  COD orders and computes status based on tolerance config; supervisor
  resolves any discrepancy via :meth:`reconcile_cod`.
- "Delivery → stock deduct" — completion fires
  ``OrderService.complete(order_id)`` inline, which emits the
  ``orders.order.completed`` event consumed by the inventory handler.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.config import get_settings
from app.core.errors import (
    BusinessRuleError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.events.outbox import enqueue_outbox
from app.core.logging import get_logger
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.deliveries.codes import make_rider_code
from app.modules.deliveries.events import (
    EVT_DELIVERY_ASSIGNED,
    EVT_DELIVERY_CANCELLED,
    EVT_DELIVERY_COD_DISCREPANCY,
    EVT_DELIVERY_COMPLETED,
    EVT_DELIVERY_DELIVERED,
    EVT_DELIVERY_FAILED,
    EVT_DELIVERY_PICKED_UP,
)
from app.modules.deliveries.models import (
    DeliveryAssignment,
    Rider,
    RiderStatus,
)
from app.modules.deliveries.repository import (
    DeliveryAssignmentRepository,
    RiderRepository,
    require_assignment,
    require_rider,
)
from app.modules.deliveries.state import (
    CodReconcileStatus,
    DeliveryStatus,
    TransitionError,
    assert_can_transition,
)
from app.modules.deliveries.storage import PodStorage, allowed_pod_mime
from app.modules.orders.models import Order, PaymentMethod
from app.modules.orders.service import OrderService
from app.modules.orders.state import OrderStatus

_logger = get_logger("hypershop.deliveries")
_CODE_RETRIES = 5
_CENTS = Decimal("100")


def _to_cents(amount: Decimal) -> int:
    return int((amount * _CENTS).quantize(Decimal("1")))


class DeliveryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.riders = RiderRepository(session)
        self.repo = DeliveryAssignmentRepository(session)
        self.storage = PodStorage()

    # ---------------- Rider admin ----------------

    async def create_rider(
        self, *, principal: Principal, fields: dict[str, Any],
    ) -> Rider:
        if not fields.get("code"):
            fields["code"] = await self._allocate_rider_code()
        r = await self.riders.create(**fields)
        await record_audit(
            actor=principal,
            action="rider.create",
            resource_type="rider",
            resource_id=r.id,
            metadata={"code": r.code, "name": r.name},
        )
        return r

    async def update_rider(
        self, *, principal: Principal, rider_id: UUID, fields: dict[str, Any],
    ) -> Rider:
        r = await self.riders.update(rider_id, **fields)
        await record_audit(
            actor=principal,
            action="rider.update",
            resource_type="rider",
            resource_id=rider_id,
            metadata={"changed": [k for k, v in fields.items() if v is not None]},
        )
        return r

    async def _allocate_rider_code(self) -> str:
        for _ in range(_CODE_RETRIES):
            candidate = make_rider_code()
            if not await self.riders.code_exists(candidate):
                return candidate
        raise BusinessRuleError("Could not allocate unique rider code.")

    async def rider_for_user(self, user_id: UUID) -> Rider:
        r = await self.riders.get_by_linked_user(user_id)
        if r is None or not r.is_active:
            raise ForbiddenError(
                "Caller is not a registered active rider.",
            )
        return r

    # ---------------- Assign ----------------

    async def assign(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        rider_id: UUID,
        notes: str | None,
    ) -> DeliveryAssignment:
        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        if OrderStatus(order.status) != OrderStatus.OUT_FOR_DELIVERY:
            raise BusinessRuleError(
                "Order must be in OUT_FOR_DELIVERY state to assign a rider.",
                details={"status": order.status},
            )
        rider = require_rider(await self.riders.get(rider_id))
        if not rider.is_active:
            raise BusinessRuleError("Rider is inactive.")

        method = PaymentMethod(order.payment_method)
        cod_expected = order.grand_total if method == PaymentMethod.COD else Decimal("0.00")
        cod_status = (
            CodReconcileStatus.PENDING.value
            if method == PaymentMethod.COD
            else CodReconcileStatus.NOT_APPLICABLE.value
        )

        assignment = await self.repo.create(
            order_id=order_id,
            rider_id=rider_id,
            status=DeliveryStatus.ASSIGNED.value,
            assigned_by=principal.user_id,
            payment_method=method.value,
            cod_expected=cod_expected,
            cod_status=cod_status,
            pod_notes=notes,
        )
        await self.repo.add_history(
            assignment_id=assignment.id,
            from_status=None,
            to_status=DeliveryStatus.ASSIGNED.value,
            transitioned_by=principal.user_id,
            reason=notes or "rider assigned",
        )
        await self.riders.update(rider_id, current_status=RiderStatus.BUSY.value)

        await record_audit(
            actor=principal,
            action="delivery.assign",
            resource_type="delivery_assignment",
            resource_id=assignment.id,
            metadata={
                "order_id": str(order_id),
                "rider_id": str(rider_id),
                "payment_method": method.value,
                "cod_expected": str(cod_expected),
            },
        )
        await enqueue_outbox(
            type=EVT_DELIVERY_ASSIGNED,
            payload={
                "assignment_id": str(assignment.id),
                "order_id": str(order_id),
                "rider_id": str(rider_id),
                "payment_method": method.value,
                "cod_expected": str(cod_expected),
            },
        )
        return require_assignment(await self.repo.get(assignment.id))

    # ---------------- Pickup ----------------

    async def pickup(
        self,
        *,
        principal: Principal,
        rider: Rider,
        assignment_id: UUID,
        notes: str | None,
    ) -> DeliveryAssignment:
        a = require_assignment(await self.repo.get_locked(assignment_id))
        if a.rider_id != rider.id:
            raise ForbiddenError("This assignment belongs to a different rider.")
        await self._transition(
            assignment=a,
            target=DeliveryStatus.PICKED_UP,
            principal=principal,
            event_type=EVT_DELIVERY_PICKED_UP,
            timestamp_field="picked_up_at",
            reason=notes or "picked up from warehouse",
        )
        return require_assignment(await self.repo.get(assignment_id))

    # ---------------- POD upload ----------------

    async def upload_pod_photo(
        self,
        *,
        principal: Principal,
        rider: Rider,
        assignment_id: UUID,
        file_bytes: bytes,
        mime: str,
    ) -> DeliveryAssignment:
        if not allowed_pod_mime(mime):
            raise ValidationError(
                "Unsupported POD photo mime — accepted: jpg/png/webp.",
                details={"mime": mime},
            )
        a = require_assignment(await self.repo.get_locked(assignment_id))
        if a.rider_id != rider.id:
            raise ForbiddenError("This assignment belongs to a different rider.")
        if a.status not in (DeliveryStatus.PICKED_UP.value, DeliveryStatus.DELIVERED.value):
            raise BusinessRuleError(
                "Can only attach a POD photo while in PICKED_UP or DELIVERED state.",
                details={"status": a.status},
            )
        relative = self.storage.relative_path_for(
            assignment_id=a.id, kind="photo", mime=mime,
        )
        self.storage.write(relative=relative, content=file_bytes)
        a.pod_photo_path = relative
        await self.session.flush()
        await record_audit(
            actor=principal,
            action="delivery.pod.upload_photo",
            resource_type="delivery_assignment",
            resource_id=a.id,
            metadata={"size": len(file_bytes), "mime": mime},
        )
        return require_assignment(await self.repo.get(assignment_id))

    # ---------------- Deliver (with POD + COD reconcile) ----------------

    async def deliver(
        self,
        *,
        principal: Principal,
        rider: Rider,
        assignment_id: UUID,
        recipient_name: str,
        pod_otp_verified: bool,
        cod_collected: Decimal | None,
        notes: str | None,
    ) -> DeliveryAssignment:
        a = require_assignment(await self.repo.get_locked(assignment_id))
        if a.rider_id != rider.id:
            raise ForbiddenError("This assignment belongs to a different rider.")
        if a.status != DeliveryStatus.PICKED_UP.value:
            raise BusinessRuleError(
                "Delivery must be PICKED_UP before it can be delivered.",
                details={"status": a.status},
            )

        # 1. POD evidence presence — hard rule.
        otp_now = utc_now() if pod_otp_verified else None
        has_evidence = (
            a.pod_photo_path is not None
            or a.pod_signature_path is not None
            or otp_now is not None
        )
        if not has_evidence:
            raise BusinessRuleError(
                "POD is mandatory — provide a photo (via upload-pod) or "
                "set pod_otp_verified=true.",
            )

        # 2. COD presence — mandatory for COD orders.
        if a.payment_method == PaymentMethod.COD.value:
            if cod_collected is None:
                raise BusinessRuleError(
                    "cod_collected is mandatory for COD deliveries.",
                )
            if cod_collected < 0:
                raise ValidationError("cod_collected cannot be negative.")
            a.cod_collected = cod_collected
            cod_final = self._reconcile_cod_amount(
                expected=a.cod_expected, collected=cod_collected,
            )
            a.cod_status = cod_final
            if cod_final == CodReconcileStatus.RECONCILED.value:
                a.cod_reconciled_at = utc_now()
                a.cod_reconciled_by = principal.user_id
            elif cod_final == CodReconcileStatus.DISCREPANCY.value:
                await enqueue_outbox(
                    type=EVT_DELIVERY_COD_DISCREPANCY,
                    payload={
                        "assignment_id": str(a.id),
                        "order_id": str(a.order_id),
                        "rider_id": str(a.rider_id),
                        "expected": str(a.cod_expected),
                        "collected": str(cod_collected),
                    },
                )
        elif cod_collected is not None and cod_collected > 0:
            raise ValidationError(
                "cod_collected must be omitted (or 0) for online-paid orders.",
            )

        a.pod_recipient_name = recipient_name
        if otp_now is not None:
            a.pod_otp_verified_at = otp_now
        if notes:
            a.pod_notes = (a.pod_notes + "\n" if a.pod_notes else "") + notes
        await self.session.flush()

        await self._transition(
            assignment=a,
            target=DeliveryStatus.DELIVERED,
            principal=principal,
            event_type=EVT_DELIVERY_DELIVERED,
            timestamp_field="delivered_at",
            reason="delivered with POD",
        )

        # If COD is settled (n/a or reconciled), advance to COMPLETED inline.
        if a.cod_status in (
            CodReconcileStatus.NOT_APPLICABLE.value,
            CodReconcileStatus.RECONCILED.value,
        ):
            await self._complete(assignment=a, principal=principal)
        return require_assignment(await self.repo.get(assignment_id))

    def _reconcile_cod_amount(
        self, *, expected: Decimal, collected: Decimal,
    ) -> str:
        cfg = get_settings()
        diff_cents = abs(_to_cents(collected) - _to_cents(expected))
        if diff_cents <= cfg.delivery_cod_auto_reconcile_tolerance_cents:
            return CodReconcileStatus.RECONCILED.value
        return CodReconcileStatus.DISCREPANCY.value

    # ---------------- Reconcile COD discrepancy ----------------

    async def reconcile_cod(
        self,
        *,
        principal: Principal,
        assignment_id: UUID,
        resolution_notes: str,
    ) -> DeliveryAssignment:
        a = require_assignment(await self.repo.get_locked(assignment_id))
        if a.cod_status != CodReconcileStatus.DISCREPANCY.value:
            raise BusinessRuleError(
                "COD reconciliation only applies to discrepancies.",
                details={"cod_status": a.cod_status},
            )
        a.cod_status = CodReconcileStatus.RESOLVED.value
        a.cod_reconciled_at = utc_now()
        a.cod_reconciled_by = principal.user_id
        a.cod_resolution_notes = resolution_notes
        await self.session.flush()

        await record_audit(
            actor=principal,
            action="delivery.cod.reconcile",
            resource_type="delivery_assignment",
            resource_id=a.id,
            metadata={
                "expected": str(a.cod_expected),
                "collected": str(a.cod_collected) if a.cod_collected is not None else None,
                "resolution_notes": resolution_notes,
            },
        )

        # If the delivery is already in DELIVERED state, the COD discrepancy
        # was the only thing blocking completion. Advance now.
        if a.status == DeliveryStatus.DELIVERED.value:
            await self._complete(assignment=a, principal=principal)
        return require_assignment(await self.repo.get(assignment_id))

    # ---------------- Cancel / Fail ----------------

    async def cancel(
        self,
        *,
        principal: Principal,
        assignment_id: UUID,
        reason: str,
    ) -> DeliveryAssignment:
        a = require_assignment(await self.repo.get_locked(assignment_id))
        a.cancellation_reason = reason
        await self._transition(
            assignment=a,
            target=DeliveryStatus.CANCELLED,
            principal=principal,
            event_type=EVT_DELIVERY_CANCELLED,
            timestamp_field="cancelled_at",
            reason=reason,
        )
        await self.riders.update(
            a.rider_id, current_status=RiderStatus.AVAILABLE.value,
        )
        return require_assignment(await self.repo.get(assignment_id))

    async def fail(
        self,
        *,
        principal: Principal,
        rider: Rider,
        assignment_id: UUID,
        reason: str,
    ) -> DeliveryAssignment:
        a = require_assignment(await self.repo.get_locked(assignment_id))
        if a.rider_id != rider.id:
            raise ForbiddenError("This assignment belongs to a different rider.")
        a.failure_reason = reason
        await self._transition(
            assignment=a,
            target=DeliveryStatus.FAILED,
            principal=principal,
            event_type=EVT_DELIVERY_FAILED,
            timestamp_field="failed_at",
            reason=reason,
        )
        await self.riders.update(
            a.rider_id, current_status=RiderStatus.AVAILABLE.value,
        )
        return require_assignment(await self.repo.get(assignment_id))

    # ---------------- Reads ----------------

    async def get_admin(self, assignment_id: UUID) -> DeliveryAssignment:
        return require_assignment(await self.repo.get(assignment_id))

    async def get_for_rider(
        self, *, rider: Rider, assignment_id: UUID,
    ) -> DeliveryAssignment:
        a = require_assignment(await self.repo.get(assignment_id))
        if a.rider_id != rider.id:
            raise ForbiddenError("Assignment does not belong to the calling rider.")
        return a

    # ============================================================
    # Rider mobile-app helpers (Module 18)
    # ============================================================

    async def set_rider_availability(
        self, *, principal: Principal, rider: Rider, status_value: str,
    ) -> Rider:
        """Rider self-toggles availability. ``busy`` is set automatically by
        ``assign``; ``offline`` / ``available`` are the rider's own choice
        and are useful for the dispatcher to filter the rider pool.
        """
        from app.modules.deliveries.models import RiderStatus

        try:
            new_status = RiderStatus(status_value)
        except ValueError as e:
            raise ValidationError(
                f"Invalid status '{status_value}'.",
                details={"allowed": [s.value for s in RiderStatus]},
            ) from e

        # Defensive: don't let the rider flip themselves to OFFLINE while a
        # delivery is in flight — supervisor must reassign first.
        if new_status == RiderStatus.OFFLINE:
            from sqlalchemy import select
            from app.modules.deliveries.models import DeliveryAssignment
            in_flight_stmt = (
                select(DeliveryAssignment.id)
                .where(
                    DeliveryAssignment.rider_id == rider.id,
                    DeliveryAssignment.status.in_(
                        ('assigned', 'picked_up', 'delivered'),
                    ),
                )
                .limit(1)
            )
            if (await self.session.execute(in_flight_stmt)).first() is not None:
                raise BusinessRuleError(
                    "Cannot go offline with active assignments. Hand them off first.",
                )

        rider.current_status = new_status.value
        await self.session.flush()
        await record_audit(
            actor=principal,
            action="delivery.rider.availability",
            resource_type="rider",
            resource_id=rider.id,
            metadata={"status": new_status.value},
        )
        return rider

    async def scan_verify(
        self,
        *,
        principal: Principal,
        rider: Rider,
        assignment_id: UUID,
        scanned_code: str,
        intent: str,  # "pickup" | "delivery"
    ) -> dict[str, Any]:
        """Verify the rider scanned the right parcel before pickup or
        before handing it over.

        The parcel label carries the order code (e.g. ``HSO-XXXXXXX``).
        We compare scanned_code to the assignment's order.code. The
        ``intent`` is informational only — both pickup and delivery
        scans verify the same code; we audit the intent separately.

        Returns ``{ok: bool, expected_code, scanned_code, status}`` so
        the rider app can show a clear pass/fail toast. We never fail
        the HTTP request itself — wrong scan returns 200 with ok=false
        so the rider can immediately retry on a noisy scan.
        """
        from app.modules.orders.models import Order

        if intent not in ("pickup", "delivery"):
            raise ValidationError(
                "intent must be 'pickup' or 'delivery'.",
            )
        a = require_assignment(await self.repo.get(assignment_id))
        if a.rider_id != rider.id:
            raise ForbiddenError(
                "Assignment does not belong to the calling rider.",
            )
        order = await self.session.get(Order, a.order_id)
        if order is None:
            raise NotFoundError("Order for this assignment not found.")
        # Normalise: strip whitespace, upper-case for case-insensitive scans.
        normalised = (scanned_code or "").strip().upper()
        ok = normalised == order.code.upper()
        await record_audit(
            actor=principal,
            action=f"delivery.scan.{intent}",
            resource_type="delivery_assignment",
            resource_id=a.id,
            outcome="success" if ok else "failure",
            metadata={
                "scanned_code": normalised,
                "expected_code": order.code,
                "intent": intent,
            },
        )
        return {
            "ok": ok,
            "expected_code": order.code,
            "scanned_code": normalised,
            "assignment_status": a.status,
            "intent": intent,
        }

    async def upload_pod_signature(
        self,
        *,
        principal: Principal,
        rider: Rider,
        assignment_id: UUID,
        file_bytes: bytes,
        mime: str,
    ) -> DeliveryAssignment:
        """Upload a signature image for POD. Same allowed mimes as the
        photo upload (jpg/png/webp). Stored alongside the photo under
        the same assignment id with ``kind='signature'``.
        """
        from app.modules.deliveries.storage import allowed_pod_mime

        if not allowed_pod_mime(mime):
            raise ValidationError(
                "Unsupported POD signature mime — accepted: jpg/png/webp.",
                details={"mime": mime},
            )
        a = require_assignment(await self.repo.get_locked(assignment_id))
        if a.rider_id != rider.id:
            raise ForbiddenError(
                "This assignment belongs to a different rider.",
            )
        if a.status not in (
            DeliveryStatus.PICKED_UP.value,
            DeliveryStatus.DELIVERED.value,
        ):
            raise BusinessRuleError(
                "Can only attach a POD signature in PICKED_UP or DELIVERED state.",
                details={"status": a.status},
            )
        relative = self.storage.relative_path_for(
            assignment_id=a.id, kind="signature", mime=mime,
        )
        self.storage.write(relative=relative, content=file_bytes)
        a.pod_signature_path = relative
        await self.session.flush()
        await record_audit(
            actor=principal,
            action="delivery.pod.upload_signature",
            resource_type="delivery_assignment",
            resource_id=a.id,
            metadata={"size": len(file_bytes), "mime": mime},
        )
        return require_assignment(await self.repo.get(assignment_id))

    async def list_today_tasks(
        self, *, rider: Rider,
    ) -> list[DeliveryAssignment]:
        """Today's task list, ordered by status then time. The rider app
        renders this as a route-friendly queue: in-flight first
        (``picked_up``), then pending pickups (``assigned``), then
        delivered-but-not-completed (awaiting COD reconciliation), then
        anything terminal that closed today.
        """
        from sqlalchemy import case, select
        from app.modules.deliveries.models import DeliveryAssignment

        now = utc_now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        ordering = case(
            (DeliveryAssignment.status == DeliveryStatus.PICKED_UP.value, 0),
            (DeliveryAssignment.status == DeliveryStatus.ASSIGNED.value, 1),
            (DeliveryAssignment.status == DeliveryStatus.DELIVERED.value, 2),
            (DeliveryAssignment.status == DeliveryStatus.COMPLETED.value, 3),
            (DeliveryAssignment.status == DeliveryStatus.FAILED.value, 4),
            (DeliveryAssignment.status == DeliveryStatus.CANCELLED.value, 5),
            else_=6,
        )
        stmt = (
            select(DeliveryAssignment)
            .where(
                DeliveryAssignment.rider_id == rider.id,
                # Anything still open OR closed today.
                (
                    DeliveryAssignment.status.in_((
                        DeliveryStatus.ASSIGNED.value,
                        DeliveryStatus.PICKED_UP.value,
                        DeliveryStatus.DELIVERED.value,
                    )) | (DeliveryAssignment.assigned_at >= start_of_day)
                ),
            )
            .order_by(ordering, DeliveryAssignment.assigned_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_next_task(
        self, *, rider: Rider,
    ) -> DeliveryAssignment | None:
        """The single next assignment the rider should act on. Picks the
        first row from ``list_today_tasks`` that is still open (not
        terminal). Returns ``None`` when the rider is idle.
        """
        for a in await self.list_today_tasks(rider=rider):
            if a.status in (
                DeliveryStatus.ASSIGNED.value,
                DeliveryStatus.PICKED_UP.value,
                DeliveryStatus.DELIVERED.value,
            ):
                return a
        return None

    async def rider_cod_summary(
        self, *, rider: Rider,
    ) -> dict[str, Any]:
        """Per-rider cash-on-hand summary for the rider app. Same numbers
        the admin sees on the finance dashboard (``rider_cash_on_hand``);
        we delegate so the rider and admin views stay in sync.
        """
        from app.modules.finance.service import FinanceService

        fs = FinanceService(self.session)
        cod = await fs.rider_cash_on_hand(rider.id)

        # Plus today's COD activity from delivery_assignments for context.
        from sqlalchemy import func, select
        from app.modules.deliveries.models import DeliveryAssignment

        now = utc_now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_collected_stmt = (
            select(
                func.coalesce(func.sum(DeliveryAssignment.cod_collected), 0),
                func.count(DeliveryAssignment.id),
            )
            .where(
                DeliveryAssignment.rider_id == rider.id,
                DeliveryAssignment.payment_method == "cod",
                DeliveryAssignment.cod_collected.isnot(None),
                DeliveryAssignment.completed_at >= start_of_day,
            )
        )
        amount_today, count_today = (
            await self.session.execute(today_collected_stmt)
        ).one()
        return {
            "rider_id": rider.id,
            "expected_total": cod["expected_total"],
            "deposited_total": cod["deposited_total"],
            "outstanding": cod["outstanding"],
            "today_collected_amount": amount_today,
            "today_collected_count": int(count_today or 0),
        }

    # ---------------- Internal ----------------

    async def _transition(
        self,
        *,
        assignment: DeliveryAssignment,
        target: DeliveryStatus,
        principal: Principal,
        event_type: str,
        reason: str,
        timestamp_field: str | None = None,
    ) -> None:
        current = DeliveryStatus(assignment.status)
        try:
            assert_can_transition(current, target)
        except TransitionError as e:
            raise BusinessRuleError(
                str(e), details={"from": e.current.value, "to": e.target.value},
            )
        assignment.status = target.value
        if timestamp_field is not None:
            setattr(assignment, timestamp_field, utc_now())
        await self.session.flush()
        await self.repo.add_history(
            assignment_id=assignment.id,
            from_status=current.value,
            to_status=target.value,
            transitioned_by=principal.user_id,
            reason=reason,
        )
        await record_audit(
            actor=principal,
            action=f"delivery.transition.{target.value}",
            resource_type="delivery_assignment",
            resource_id=assignment.id,
            metadata={"from": current.value, "to": target.value, "reason": reason},
        )
        await enqueue_outbox(
            type=event_type,
            payload={
                "assignment_id": str(assignment.id),
                "order_id": str(assignment.order_id),
                "rider_id": str(assignment.rider_id),
                "from_status": current.value,
                "to_status": target.value,
                "reason": reason,
            },
        )

    async def _complete(
        self,
        *,
        assignment: DeliveryAssignment,
        principal: Principal,
    ) -> None:
        """Final step — move delivery to COMPLETED and inline-call the
        orders service so the order also moves to COMPLETED. That, in
        turn, emits ``orders.order.completed`` which the inventory
        handler consumes — draining reserved stock. **This is the
        "delivery → stock deduct" rule, end-to-end.**
        """
        await self._transition(
            assignment=assignment,
            target=DeliveryStatus.COMPLETED,
            principal=principal,
            event_type=EVT_DELIVERY_COMPLETED,
            timestamp_field="completed_at",
            reason="delivery completed",
        )
        await self.riders.update(
            assignment.rider_id, current_status=RiderStatus.AVAILABLE.value,
        )
        # Inline order completion — same transaction. If the order isn't in
        # OUT_FOR_DELIVERY (e.g. admin already marked it complete
        # manually) the orders service will raise; we let it propagate
        # rather than swallow because that's a real consistency bug.
        await OrderService(self.session).complete(
            principal=principal,
            order_id=assignment.order_id,
            reason="delivery completed",
        )
