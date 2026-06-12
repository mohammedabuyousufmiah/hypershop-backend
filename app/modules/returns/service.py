"""Return-request service.

Lifecycle entry points
----------------------
- :meth:`request` — customer (or admin on customer's behalf) opens a
  return for a delivered order. Snapshots line names/skus from the order
  so later product edits don't change the historical record.
- :meth:`receive` — warehouse marks the package physically arrived.
- :meth:`inspect` — inspector records per-line ``condition`` (sealed /
  opened / cold_chain_broken / expired), the actual ``inspected_quantity``,
  and the ``target_batch_id`` the unit will land in.
- :meth:`complete` — applies the inventory movement for every line based
  on its condition (the rule mapping):

  | Condition           | Bucket    | Effect                           |
  |---------------------|-----------|----------------------------------|
  | sealed              | available | restocked, sellable again         |
  | opened              | blocked   | quarantined for write-off         |
  | cold_chain_broken   | blocked   | quarantined for write-off         |
  | expired             | expired   | tracked as disposal write-off     |

- :meth:`reject` — admin refuses the return request (terminal).
- :meth:`cancel_by_customer` / :meth:`cancel_by_admin` — voids.

Hard-rule mappings
------------------
- "Sealed → return stock" — completion writes a ``+available`` ledger
  row of kind ``return``.
- "Opened → block" — completion writes ``+blocked`` ledger row.
- "Cold-chain broken → block" — same as opened, different audit reason.
- "Expired → disposal" — completion writes ``+expired`` ledger row,
  effectively a write-off captured in inventory history.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
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
from app.modules.inventory.models import LedgerKind
from app.modules.inventory.repository import StockRepository
from app.modules.inventory.repository import WarehouseRepository
from app.modules.orders.models import Order, OrderLine
from app.modules.orders.state import OrderStatus
from app.modules.returns.codes import make_return_code
from app.modules.returns.events import (
    EVT_RETURN_CANCELLED,
    EVT_RETURN_COMPLETED,
    EVT_RETURN_INSPECTED,
    EVT_RETURN_RECEIVED,
    EVT_RETURN_REJECTED,
    EVT_RETURN_REQUESTED,
)
from app.modules.returns.models import ReturnRequest, ReturnRequestLine
from app.modules.returns.repository import ReturnRepository, require_request
from app.modules.returns.state import (
    CUSTOMER_CANCELLABLE,
    ReturnAction,
    ReturnCondition,
    ReturnStatus,
    TransitionError,
    action_for_condition,
    assert_can_transition,
    bucket_for_action,
)

_logger = get_logger("hypershop.returns")
_CODE_RETRIES = 5


class ReturnService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ReturnRepository(session)
        self.stock = StockRepository(session)
        self.warehouses = WarehouseRepository(session)

    # ---------------- Request ----------------

    async def request(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        reason: str,
        lines: list[dict[str, Any]],
        notes: str | None,
    ) -> ReturnRequest:
        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        if order.customer_user_id != principal.user_id:
            raise ForbiddenError("Order does not belong to you.")
        if OrderStatus(order.status) != OrderStatus.COMPLETED:
            raise BusinessRuleError(
                "Returns are only allowed for completed orders.",
                details={"order_status": order.status},
            )

        # Snapshot order lines we'll be returning.
        order_line_ids = [li["order_line_id"] for li in lines]
        ol_stmt = select(OrderLine).where(
            OrderLine.id.in_(order_line_ids),
            OrderLine.order_id == order_id,
        )
        order_lines_by_id: dict[UUID, OrderLine] = {
            ol.id: ol for ol in (await self.session.execute(ol_stmt)).scalars().all()
        }
        if len(order_lines_by_id) != len(set(order_line_ids)):
            raise NotFoundError("One or more order lines not found in this order.")

        for li in lines:
            ol = order_lines_by_id[li["order_line_id"]]
            if li["quantity"] > ol.quantity:
                raise BusinessRuleError(
                    "Return quantity exceeds the ordered quantity for a line.",
                    details={
                        "order_line_id": str(ol.id),
                        "ordered": ol.quantity,
                        "requested": li["quantity"],
                    },
                )

        code = await self._allocate_code()
        rr = await self.repo.create(
            code=code,
            order_id=order_id,
            customer_user_id=order.customer_user_id,
            status=ReturnStatus.REQUESTED.value,
            reason=reason,
            requested_by=principal.user_id,
            notes=notes,
        )
        for li in lines:
            ol = order_lines_by_id[li["order_line_id"]]
            await self.repo.add_line(
                return_request_id=rr.id,
                order_line_id=ol.id,
                variant_id=ol.variant_id,
                product_name=ol.product_name,
                variant_sku=ol.variant_sku,
                requested_quantity=li["quantity"],
            )
        await self.repo.add_history(
            return_request_id=rr.id,
            from_status=None,
            to_status=ReturnStatus.REQUESTED.value,
            transitioned_by=principal.user_id,
            reason="customer requested return",
        )
        await record_audit(
            actor=principal,
            action="returns.return.request",
            resource_type="return_request",
            resource_id=rr.id,
            metadata={
                "code": code,
                "order_id": str(order_id),
                "line_count": len(lines),
            },
        )
        await enqueue_outbox(
            type=EVT_RETURN_REQUESTED,
            payload={
                "return_request_id": str(rr.id),
                "code": code,
                "order_id": str(order_id),
                "customer_user_id": str(order.customer_user_id),
            },
        )
        return require_request(await self.repo.get(rr.id))

    async def _allocate_code(self) -> str:
        for _ in range(_CODE_RETRIES):
            candidate = make_return_code()
            if not await self.repo.code_exists(candidate):
                return candidate
        raise BusinessRuleError("Could not allocate a unique return code.")

    # ---------------- Receive ----------------

    async def receive(
        self,
        *,
        principal: Principal,
        request_id: UUID,
        notes: str | None,
    ) -> ReturnRequest:
        rr = require_request(await self.repo.get_locked(request_id))
        await self._transition(
            request=rr,
            target=ReturnStatus.RECEIVED,
            principal=principal,
            event_type=EVT_RETURN_RECEIVED,
            timestamp_field="received_at",
            user_field="received_by",
            reason=notes or "package received at warehouse",
        )
        return require_request(await self.repo.get(request_id))

    # ---------------- Inspect ----------------

    async def inspect(
        self,
        *,
        principal: Principal,
        request_id: UUID,
        line_inspections: list[dict[str, Any]],
    ) -> ReturnRequest:
        rr = require_request(await self.repo.get_locked(request_id))
        if rr.status != ReturnStatus.RECEIVED.value:
            raise BusinessRuleError(
                "Return must be in RECEIVED state to inspect.",
                details={"status": rr.status},
            )

        lines_by_id: dict[UUID, ReturnRequestLine] = {
            li.id: li for li in rr.lines
        }
        # Validate every requested line is inspected.
        provided_ids = {entry["line_id"] for entry in line_inspections}
        if provided_ids != set(lines_by_id):
            raise ValidationError(
                "Inspection must cover every line of the return — exactly once each.",
                details={
                    "expected_lines": [str(i) for i in lines_by_id],
                    "provided_lines": [str(i) for i in provided_ids],
                },
            )

        for entry in line_inspections:
            line = lines_by_id[entry["line_id"]]
            if entry["inspected_quantity"] > line.requested_quantity:
                raise BusinessRuleError(
                    "Inspected quantity cannot exceed requested quantity.",
                    details={
                        "line_id": str(line.id),
                        "requested": line.requested_quantity,
                        "inspected": entry["inspected_quantity"],
                    },
                )
            wh = await self.warehouses.get_by_code(entry["target_warehouse_code"])
            if wh is None:
                raise NotFoundError(
                    f"Warehouse '{entry['target_warehouse_code']}' not found.",
                )
            line.inspected_quantity = entry["inspected_quantity"]
            line.condition = entry["condition"]
            line.target_batch_id = entry["target_batch_id"]
            line.target_warehouse_id = wh.id
            if entry.get("inspection_notes"):
                line.inspection_notes = entry["inspection_notes"]
        await self.session.flush()

        await self._transition(
            request=rr,
            target=ReturnStatus.INSPECTED,
            principal=principal,
            event_type=EVT_RETURN_INSPECTED,
            timestamp_field="inspected_at",
            user_field="inspected_by",
            reason="per-line conditions recorded",
        )
        return require_request(await self.repo.get(request_id))

    # ---------------- Complete (apply inventory) ----------------

    async def complete(
        self,
        *,
        principal: Principal,
        request_id: UUID,
    ) -> ReturnRequest:
        rr = require_request(await self.repo.get_locked(request_id))
        if rr.status != ReturnStatus.INSPECTED.value:
            raise BusinessRuleError(
                "Return must be in INSPECTED state to complete.",
                details={"status": rr.status},
            )

        per_action: dict[str, int] = {a.value: 0 for a in ReturnAction}
        for line in rr.lines:
            if line.inspected_quantity is None or line.inspected_quantity <= 0:
                # Nothing physically arrived for this line — record action
                # and move on. No inventory write.
                continue
            if line.condition is None or line.target_batch_id is None or line.target_warehouse_id is None:
                raise BusinessRuleError(
                    "A line is missing inspection data — cannot complete.",
                    details={"line_id": str(line.id)},
                )
            condition = ReturnCondition(line.condition)
            action = action_for_condition(condition)
            bucket = bucket_for_action(action)
            await self.stock.apply_movement(
                variant_id=line.variant_id,
                batch_id=line.target_batch_id,
                warehouse_id=line.target_warehouse_id,
                bucket=bucket,
                quantity_delta=line.inspected_quantity,
                kind=LedgerKind.RETURN,
                actor_id=principal.user_id,
                reference_type="return_request",
                reference_id=rr.id,
                notes=f"return condition={condition.value}; action={action.value}",
                extra={
                    "return_request_id": str(rr.id),
                    "return_request_line_id": str(line.id),
                    "condition": condition.value,
                },
            )
            line.applied_action = action.value
            line.applied_at = utc_now()
            per_action[action.value] += line.inspected_quantity
        await self.session.flush()

        await self._transition(
            request=rr,
            target=ReturnStatus.COMPLETED,
            principal=principal,
            event_type=EVT_RETURN_COMPLETED,
            timestamp_field="completed_at",
            user_field="completed_by",
            reason="inventory applied",
            extra_payload={"per_action": per_action},
        )
        return require_request(await self.repo.get(request_id))

    # ---------------- Reject / Cancel ----------------

    async def reject(
        self,
        *,
        principal: Principal,
        request_id: UUID,
        reason: str,
    ) -> ReturnRequest:
        rr = require_request(await self.repo.get_locked(request_id))
        rr.rejection_reason = reason
        await self._transition(
            request=rr,
            target=ReturnStatus.REJECTED,
            principal=principal,
            event_type=EVT_RETURN_REJECTED,
            timestamp_field=None,
            user_field=None,
            reason=reason,
        )
        return require_request(await self.repo.get(request_id))

    async def cancel_by_customer(
        self,
        *,
        principal: Principal,
        request_id: UUID,
        reason: str,
    ) -> ReturnRequest:
        rr = require_request(await self.repo.get_locked(request_id))
        if rr.customer_user_id != principal.user_id:
            raise ForbiddenError("This return does not belong to you.")
        if ReturnStatus(rr.status) not in CUSTOMER_CANCELLABLE:
            raise BusinessRuleError(
                "Customer can no longer cancel this return.",
                details={"status": rr.status},
            )
        rr.cancellation_reason = reason
        await self._transition(
            request=rr,
            target=ReturnStatus.CANCELLED,
            principal=principal,
            event_type=EVT_RETURN_CANCELLED,
            timestamp_field="cancelled_at",
            user_field=None,
            reason=reason,
        )
        return require_request(await self.repo.get(request_id))

    async def cancel_by_admin(
        self,
        *,
        principal: Principal,
        request_id: UUID,
        reason: str,
    ) -> ReturnRequest:
        rr = require_request(await self.repo.get_locked(request_id))
        rr.cancellation_reason = reason
        await self._transition(
            request=rr,
            target=ReturnStatus.CANCELLED,
            principal=principal,
            event_type=EVT_RETURN_CANCELLED,
            timestamp_field="cancelled_at",
            user_field=None,
            reason=reason,
        )
        return require_request(await self.repo.get(request_id))

    # ---------------- Reads ----------------

    async def get_for_customer(
        self, *, principal: Principal, request_id: UUID,
    ) -> ReturnRequest:
        rr = require_request(await self.repo.get(request_id))
        if rr.customer_user_id != principal.user_id:
            raise ForbiddenError("This return does not belong to you.")
        return rr

    async def get_admin(self, request_id: UUID) -> ReturnRequest:
        return require_request(await self.repo.get(request_id))

    # ---------------- Internal ----------------

    async def _transition(
        self,
        *,
        request: ReturnRequest,
        target: ReturnStatus,
        principal: Principal,
        event_type: str,
        timestamp_field: str | None,
        user_field: str | None,
        reason: str,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        current = ReturnStatus(request.status)
        try:
            assert_can_transition(current, target)
        except TransitionError as e:
            raise BusinessRuleError(
                str(e), details={"from": e.current.value, "to": e.target.value},
            )
        request.status = target.value
        if timestamp_field is not None:
            setattr(request, timestamp_field, utc_now())
        if user_field is not None:
            setattr(request, user_field, principal.user_id)
        await self.session.flush()
        await self.repo.add_history(
            return_request_id=request.id,
            from_status=current.value,
            to_status=target.value,
            transitioned_by=principal.user_id,
            reason=reason,
        )
        await record_audit(
            actor=principal,
            action=f"returns.return.transition.{target.value}",
            resource_type="return_request",
            resource_id=request.id,
            metadata={"from": current.value, "to": target.value, "reason": reason},
        )
        payload: dict[str, Any] = {
            "return_request_id": str(request.id),
            "code": request.code,
            "order_id": str(request.order_id),
            "customer_user_id": str(request.customer_user_id),
            "from_status": current.value,
            "to_status": target.value,
        }
        if extra_payload:
            payload.update(extra_payload)
        await enqueue_outbox(type=event_type, payload=payload)
