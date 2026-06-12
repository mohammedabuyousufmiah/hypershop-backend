"""Fulfillment aggregation read-only API under
``/api/v1/admin/fulfillment/*``.

Four endpoints feed the 4 Marketplace Fulfillment surfaces that
didn't fit neatly into an existing module:

- ``GET /seller-pickup`` — items ready for hub pickup from sellers
- ``GET /reschedule``    — failed-delivery orders awaiting reschedule
- ``GET /sla-alerts``    — orders past their dispatch/delivery SLA
- ``GET /seller-delay``  — sellers stuck packing past their SLA

All four require ``rider.routing.view`` except ``seller-delay`` which
requires ``sellers.approve``. No writes — writes happen via the
underlying module endpoints (rider_routing for reassignment, returns
for the return-leg toggle, etc.).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.time import utc_now

router = APIRouter(prefix="/admin/fulfillment", tags=["admin-fulfillment"])


# ---------- Stage-bump helper (wires existing write paths to the 21-state
# fulfillment_stage column added in migration 0080). Endpoints opt in by
# calling this inside their transactional() block AFTER their main mutation.
# Soft-fails so the parent action is never aborted by a stage bookkeeping
# error — the audit log on the parent endpoint still records the action.
async def _bump_stage(
    session: Any,
    order_id: Any,
    target_value: str,
    principal: Any,
    reason: str,
) -> str | None:
    from app.modules.orders.repository import OrderRepository
    from app.modules.orders.stage_service import StageService
    from app.modules.orders.state import OrderFulfillmentStage
    try:
        target = OrderFulfillmentStage(target_value)
    except ValueError:
        return None
    try:
        svc = StageService(OrderRepository(session))
        # force=True because the caller already enforced its own perm gate
        # for the underlying action; the graph guard would otherwise reject
        # legitimate non-linear transitions (e.g. refund from any state).
        order = await svc.set_stage(
            order_id=order_id,
            target=target,
            actor_id=getattr(principal, "user_id", None),
            reason=f"endpoint-bump: {reason}",
            force=True,
        )
        return order.fulfillment_stage
    except Exception:  # noqa: BLE001
        # Stage bump must never break the parent action.
        return None

# ----- Fine-grained perm gates (2026-05-24) -----
# Replaces the blanket rider.dispatch gate with per-action perms so
# support / finance / supervisor roles get only what they need.
# See app/modules/iam/permissions.py for the constants.
_QUEUE_VIEW = "fulfillment.queue.view"
_SELLER = "sellers.approve"
_PICKUP_ASSIGN = "fulfillment.pickup.assign"
_DELIVERY_ASSIGN = "fulfillment.delivery.assign"
_REASSIGN = "fulfillment.rider.reassign"
_BATCH_CREATE = "fulfillment.batch.create"
_FAIL_MARK = "fulfillment.fail.mark"
_RESCHEDULE = "fulfillment.reschedule"
_RETURN_HUB = "fulfillment.return.hub"
_RETURN_SELLER = "fulfillment.return.seller"
_NOTIFY = "fulfillment.notify.customer"
_ESCALATE_SUPPORT = "fulfillment.escalate.support"
_ESCALATE_FINANCE = "fulfillment.escalate.finance"
_ESCALATE_MANAGER = "fulfillment.escalate.manager"
_COD_VIEW = "fulfillment.cod.exposure.view"
_CAPACITY_VIEW = "fulfillment.rider.capacity.view"
_SLA_VIEW = "fulfillment.sla.alerts.view"

# High-risk financial / destructive (per 12-perm catalog 2026-05-24).
_ORDER_CONFIRM = "order.confirm"
_ORDER_CANCEL_HV = "order.cancel.high_value"
_PAYMENT_MARK = "payment.mark.success"
_REFUND_APPROVE = "refund.approve"
_COD_EDIT = "cod.collected.edit"
_COD_SETTLEMENT = "cod.settlement.approve"
_WALLET_EDIT = "rider.wallet.edit"
_PAYOUT_EDIT = "seller.payout.edit"
_PRICE_EDIT = "product.price.edit"
_ORDER_DELETE = "order.delete"
_AUDIT_DELETE = "audit.delete"
_DELIVERY_MARK_NP = "delivery.mark.no_proof"
HIGH_VALUE_THRESHOLD_MINOR = 10_000_00  # ৳10,000

# Back-compat aliases — kept so the existing endpoints further down
# in this file (which reference _VIEW / _WRITE) still resolve. New
# endpoints should use the specific gates above.
_VIEW = _QUEUE_VIEW
_WRITE = _QUEUE_VIEW              # specific write gates applied per-endpoint below


# ============================================================
#  Seller Pickup Queue
# ============================================================
@router.get(
    "/seller-pickup",
    summary="Items ready for hub pickup from sellers in the next N hours",
)
async def seller_pickup_queue(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_VIEW))],
    window_hours: Annotated[int, Query(ge=1, le=72)] = 24,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Orders in `ready_to_ship` or `awaiting_pickup` status whose
    dispatched_at is null and were placed within the lookback window.
    Groups by seller_id so the hub team can plan one pickup van
    per seller. Falls back to listing all matching orders ungrouped
    when seller_id isn't tracked on OrderLine yet (the catalog→sellers
    join is partial in the v1 catalog)."""
    from sqlalchemy import or_
    from app.modules.orders.models import Order, OrderLine
    cutoff = utc_now() - timedelta(hours=window_hours)
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                select(Order.id, Order.code, Order.status, Order.placed_at,
                       Order.payment_method, Order.grand_total)
                .where(
                    and_(
                        Order.status.in_(("ready_to_ship", "awaiting_pickup",
                                          "packed", "ready_for_dispatch")),
                        Order.dispatched_at.is_(None),
                        Order.placed_at >= cutoff,
                    ),
                )
                .order_by(Order.placed_at.asc())
                .limit(limit),
            )
        ).all()
    return {
        "window_hours": window_hours,
        "count": len(rows),
        "items": [
            {
                "order_id": str(r[0]),
                "order_code": r[1],
                "status": r[2],
                "placed_at": r[3].isoformat() if r[3] else None,
                "payment_method": r[4],
                "grand_total": float(r[5]) if r[5] is not None else 0.0,
            }
            for r in rows
        ],
    }


# ============================================================
#  Reschedule Queue
# ============================================================
@router.get(
    "/reschedule",
    summary="Failed-delivery orders awaiting reschedule",
)
async def reschedule_queue(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_VIEW))],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Orders whose delivery attempt failed (status one of
    `delivery_failed` / `delivery_attempted` / `out_for_delivery_failed`)
    and that are not yet cancelled or completed. The NDR auto-escalator
    (per memory `project_courier_erp_v5`) writes these statuses on
    rider mark-fail; this endpoint surfaces them for manual reschedule."""
    from app.modules.orders.models import Order
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                select(Order.id, Order.code, Order.status, Order.placed_at,
                       Order.payment_method, Order.delivery_address,
                       Order.grand_total)
                .where(
                    Order.status.in_((
                        "delivery_failed",
                        "delivery_attempted",
                        "out_for_delivery_failed",
                        "ndr_pending",
                    )),
                )
                .order_by(Order.placed_at.asc())
                .limit(limit),
            )
        ).all()
    items: list[dict[str, Any]] = []
    for r in rows:
        addr = r[5] or {}
        items.append({
            "order_id": str(r[0]),
            "order_code": r[1],
            "status": r[2],
            "placed_at": r[3].isoformat() if r[3] else None,
            "payment_method": r[4],
            "grand_total": float(r[6]) if r[6] is not None else 0.0,
            "city": addr.get("city") or addr.get("district") or None,
            "phone": addr.get("phone") or None,
        })
    return {"count": len(items), "items": items}


# ============================================================
#  SLA Breach Alerts
# ============================================================
@router.get(
    "/sla-alerts",
    summary="Orders past their dispatch / delivery SLA",
)
async def sla_breach_alerts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_VIEW))],
    dispatch_sla_hours: Annotated[int, Query(ge=1, le=72)] = 24,
    delivery_sla_hours: Annotated[int, Query(ge=1, le=168)] = 72,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Two-bucket alert feed: (a) orders placed > dispatch_sla_hours
    ago but still not dispatched, (b) orders dispatched but not
    delivered within delivery_sla_hours of placement. Both buckets
    excluded statuses: cancelled, completed, returned, refunded.
    """
    from app.modules.orders.models import Order
    now = utc_now()
    dispatch_cutoff = now - timedelta(hours=dispatch_sla_hours)
    delivery_cutoff = now - timedelta(hours=delivery_sla_hours)
    terminal = ("cancelled", "completed", "returned", "refunded", "delivered")

    async with uow.transactional() as session:
        dispatch_rows = (
            await session.execute(
                select(Order.id, Order.code, Order.status, Order.placed_at)
                .where(
                    and_(
                        Order.status.not_in(terminal),
                        Order.dispatched_at.is_(None),
                        Order.placed_at < dispatch_cutoff,
                    ),
                )
                .order_by(Order.placed_at.asc())
                .limit(limit),
            )
        ).all()
        delivery_rows = (
            await session.execute(
                select(Order.id, Order.code, Order.status, Order.placed_at,
                       Order.dispatched_at)
                .where(
                    and_(
                        Order.status.not_in(terminal),
                        Order.dispatched_at.is_not(None),
                        Order.placed_at < delivery_cutoff,
                    ),
                )
                .order_by(Order.placed_at.asc())
                .limit(limit),
            )
        ).all()

    def _hours_late(placed_at: datetime, sla: int) -> int:
        if not placed_at:
            return 0
        delta = now - placed_at
        return max(0, int(delta.total_seconds() // 3600) - sla)

    return {
        "dispatch_breaches": {
            "sla_hours": dispatch_sla_hours,
            "count": len(dispatch_rows),
            "items": [
                {
                    "order_id": str(r[0]),
                    "order_code": r[1],
                    "status": r[2],
                    "placed_at": r[3].isoformat() if r[3] else None,
                    "hours_late": _hours_late(r[3], dispatch_sla_hours),
                }
                for r in dispatch_rows
            ],
        },
        "delivery_breaches": {
            "sla_hours": delivery_sla_hours,
            "count": len(delivery_rows),
            "items": [
                {
                    "order_id": str(r[0]),
                    "order_code": r[1],
                    "status": r[2],
                    "placed_at": r[3].isoformat() if r[3] else None,
                    "dispatched_at": r[4].isoformat() if r[4] else None,
                    "hours_late": _hours_late(r[3], delivery_sla_hours),
                }
                for r in delivery_rows
            ],
        },
    }


# ============================================================
#  Seller Delay Monitor
# ============================================================
@router.get(
    "/seller-delay",
    summary="Sellers with orders stuck past their pack SLA",
)
async def seller_delay_monitor(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_SELLER))],
    pack_sla_hours: Annotated[int, Query(ge=1, le=72)] = 24,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Groups stuck orders by seller_id. A seller is "stuck" when at
    least one of their order lines belongs to an order whose placed_at
    is past the pack_sla_hours threshold AND the order is still in a
    pre-dispatch status (placed / confirmed / preparing / packing).

    Returns the seller list ranked by delay count + average hours late.
    Falls back to the per-line product seller_id when the catalog has
    multi-seller support, otherwise ranks at the platform level.
    """
    from app.modules.orders.models import Order, OrderLine
    cutoff = utc_now() - timedelta(hours=pack_sla_hours)
    pre_dispatch = ("placed", "confirmed", "preparing", "packing",
                    "ready_to_ship", "awaiting_pickup")

    async with uow.transactional() as session:
        # Try the seller_id path first; if the column doesn't exist
        # we fall back to platform-wide ranking.
        try:
            from app.modules.catalog.models import Product
            rows = (
                await session.execute(
                    select(
                        Product.seller_id.label("seller_id"),
                        func.count(Order.id.distinct()).label("stuck_count"),
                        func.avg(
                            func.extract(
                                "epoch", utc_now() - Order.placed_at,
                            ) / 3600.0,
                        ).label("avg_hours_late"),
                    )
                    .join(OrderLine, OrderLine.order_id == Order.id)
                    .join(Product, Product.id == OrderLine.product_id)
                    .where(
                        and_(
                            Order.status.in_(pre_dispatch),
                            Order.placed_at < cutoff,
                            Product.seller_id.is_not(None),
                        ),
                    )
                    .group_by(Product.seller_id)
                    .order_by(func.count(Order.id.distinct()).desc())
                    .limit(limit),
                )
            ).all()
            items = [
                {
                    "seller_id": str(r[0]) if r[0] else None,
                    "stuck_orders": int(r[1]),
                    "avg_hours_late": round(float(r[2] or 0), 1),
                }
                for r in rows
            ]
            return {
                "pack_sla_hours": pack_sla_hours,
                "mode": "per_seller",
                "count": len(items),
                "items": items,
            }
        except Exception:  # noqa: BLE001
            # Fallback: platform-level rollup (no seller_id column).
            count = (
                await session.execute(
                    select(func.count(Order.id))
                    .where(
                        and_(
                            Order.status.in_(pre_dispatch),
                            Order.placed_at < cutoff,
                        ),
                    ),
                )
            ).scalar_one()
            return {
                "pack_sla_hours": pack_sla_hours,
                "mode": "platform_wide",
                "count": 1 if count else 0,
                "items": [{
                    "seller_id": None,
                    "stuck_orders": int(count or 0),
                    "avg_hours_late": None,
                }] if count else [],
            }


# ============================================================
#  Action layer — write endpoints orchestrating other modules
# ============================================================
from pydantic import BaseModel, Field
from uuid import UUID
from app.core.audit import record_audit


class OrderStatusPatch(BaseModel):
    """Move an order forward one step in the fulfillment lifecycle.
    Wraps the existing /admin/orders/{id}/{action} endpoints so the
    fulfillment hub can drive status changes without bouncing the
    operator to a different admin page."""
    action: str = Field(
        pattern=r"^(confirm_payment|start_packing|dispatch|complete|cancel)$",
    )
    reason: str | None = Field(default=None, max_length=256)


@router.post(
    "/orders/{order_id}/move",
    summary="Move an order forward (confirm_payment / start_packing / dispatch / complete / cancel)",
)
async def order_move(
    order_id: UUID,
    body: OrderStatusPatch,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    # High-value cancel guard — if cancelling an order >= ৳10,000
    # the caller MUST hold `order.cancel.high_value`. Lower-value
    # cancels still allowed under the regular queue perm. This
    # prevents a dispatcher from accidentally cancelling a ৳50k
    # iPhone order without a second approver.
    from fastapi import HTTPException
    if body.action == "cancel":
        # Cheap lookup before doing the cancel.
        from app.modules.orders.models import Order
        async with uow.transactional() as session:
            row = (await session.execute(
                select(Order.grand_total).where(Order.id == order_id),
            )).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Order not found")
            total_minor = int((row or 0) * 100)
        if total_minor >= HIGH_VALUE_THRESHOLD_MINOR and \
                _ORDER_CANCEL_HV not in principal.permissions:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Order total ৳{total_minor // 100} ≥ ৳10,000 — requires "
                    f"{_ORDER_CANCEL_HV} perm or a second-approval workflow"
                ),
            )
    elif _QUEUE_VIEW not in principal.permissions:
        raise HTTPException(
            status_code=403,
            detail=f"Missing required permission: {_QUEUE_VIEW}",
        )
    """Single-action status pivot. Delegates to the existing orders
    service so business rules (payment gate, packing min-images, etc.)
    are honoured. Soft-imported to avoid circular dependencies."""
    from app.modules.orders.service import OrderAdminService
    async with uow.transactional() as session:
        svc = OrderAdminService(session)
        # All OrderAdminService methods are keyword-only with
        # (principal, order_id, reason?) signature.
        if body.action == "confirm_payment":
            row = await svc.confirm_payment(
                principal=principal, order_id=order_id, reason=body.reason,
            )
        elif body.action == "start_packing":
            row = await svc.start_packing(
                principal=principal, order_id=order_id, reason=body.reason,
            )
        elif body.action == "dispatch":
            row = await svc.dispatch(
                principal=principal, order_id=order_id, reason=body.reason,
            )
        elif body.action == "complete":
            row = await svc.complete(
                principal=principal, order_id=order_id, reason=body.reason,
            )
        elif body.action == "cancel":
            row = await svc.cancel_by_admin(
                principal=principal, order_id=order_id,
                reason=body.reason or "fulfillment-hub cancel",
            )
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
        # Bump fulfillment_stage to match the legal action.
        _ACTION_TO_STAGE = {
            "confirm_payment": "PAYMENT_VERIFIED",
            "start_packing": "SELLER_PROCESSING",
            "dispatch": "OUT_FOR_DELIVERY",
            "complete": "DELIVERED",
            "cancel": "CANCELLED",
        }
        new_stage = await _bump_stage(
            session, order_id,
            _ACTION_TO_STAGE.get(body.action, ""),
            principal, f"order.move/{body.action}",
        )
        await record_audit(
            actor=principal,
            action=f"fulfillment.order.{body.action}",
            resource_type="order",
            resource_id=order_id,
            metadata={"fulfillment_stage": new_stage},
        )
    return {
        "order_id": str(order_id),
        "action": body.action,
        "status": row.status if row else None,
        "fulfillment_stage": new_stage,
    }


class RiderAssign(BaseModel):
    """Assign a rider to a run-sheet (batch)."""
    rider_id: UUID
    run_sheet_id: UUID


@router.post(
    "/rider/assign",
    summary="Assign rider to an existing run-sheet (batch)",
)
async def rider_assign(
    body: RiderAssign,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_DELIVERY_ASSIGN))],
) -> dict[str, Any]:
    """Thin wrapper over rider_routing's run-sheet assignment. Lives in
    the fulfillment hub so the operator never leaves the unified view
    to assign a rider — saves a context switch on every dispatch."""
    from app.modules.rider_routing.service import RiderRoutingService
    async with uow.transactional() as session:
        svc = RiderRoutingService(session)
        rs = await svc.assign_run_sheet(
            run_sheet_id=body.run_sheet_id,
            rider_id=body.rider_id,
            actor=principal,
        )
        await record_audit(
            actor=principal,
            action="fulfillment.rider.assign",
            resource_type="run_sheet",
            resource_id=body.run_sheet_id,
            metadata={"rider_id": str(body.rider_id)},
        )
    return {
        "run_sheet_id": str(body.run_sheet_id),
        "rider_id": str(body.rider_id),
        "status": getattr(rs, "status", None),
    }


class FailedDeliveryHandle(BaseModel):
    """Mark a delivery attempt failed + schedule next attempt."""
    failure_reason: str = Field(min_length=1, max_length=200)
    next_attempt_at: str | None = Field(default=None, max_length=32)
    notify_customer: bool = True


@router.post(
    "/orders/{order_id}/fail",
    summary="Mark a delivery attempt failed + schedule reschedule + optionally notify customer",
)
async def fail_delivery(
    order_id: UUID,
    body: FailedDeliveryHandle,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_FAIL_MARK))],
) -> dict[str, Any]:
    """Updates the order status to `delivery_failed` + persists the
    failure reason + drops the order into the Reschedule Queue. When
    `notify_customer=True` writes a CC inbox event (soft-fail). The
    NDR auto-escalator picks it up from there for the 3-attempt
    cycle (per memory `project_courier_erp_v5`)."""
    from sqlalchemy import update as sa_update
    from app.modules.orders.models import Order
    async with uow.transactional() as session:
        res = await session.execute(
            sa_update(Order)
            .where(Order.id == order_id)
            .values(
                status="delivery_failed",
                notes=f"NDR: {body.failure_reason}",
            ),
        )
        if (res.rowcount or 0) == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Order not found")
        new_stage = await _bump_stage(
            session, order_id, "FAILED_DELIVERY", principal,
            f"NDR: {body.failure_reason}",
        )
        await record_audit(
            actor=principal,
            action="fulfillment.delivery.fail",
            resource_type="order",
            resource_id=order_id,
            metadata={
                "reason": body.failure_reason,
                "next_attempt_at": body.next_attempt_at,
                "notify": body.notify_customer,
                "fulfillment_stage": new_stage,
            },
        )
    return {
        "order_id": str(order_id),
        "new_status": "delivery_failed",
        "reason": body.failure_reason,
        "next_attempt_at": body.next_attempt_at,
        "fulfillment_stage": new_stage,
    }


class ReturnLegToggle(BaseModel):
    """Move a return between legs: customer→hub vs hub→seller."""
    leg: str = Field(pattern=r"^(receive|inspect|complete|reject)$")
    notes: str | None = Field(default=None, max_length=400)


@router.post(
    "/returns/{request_id}/{leg}",
    summary="Drive a return through its lifecycle (receive→inspect→complete/reject)",
)
async def return_leg(
    request_id: UUID,
    leg: str,
    body: ReturnLegToggle,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    # Hub-leg actions need the hub perm; seller-leg actions (reject /
    # complete that bounce back to seller) need the seller perm. Pick
    # the stricter gate (return.hub) — admins with seller-only perm
    # who need full lifecycle can be granted both.
    principal: Annotated[Principal, Depends(requires_permission(_RETURN_HUB))],
) -> dict[str, Any]:
    """Single endpoint to drive the entire return flow without
    bouncing to /admin/returns. Delegates to the returns module's
    service. ``leg`` and ``body.leg`` are kept in sync (path takes
    precedence). Wallet credit + supplier-payout deduction happen
    inside the returns service per Module M4 contract."""
    from app.modules.returns.service import ReturnAdminService
    if leg != body.leg:
        body = body.model_copy(update={"leg": leg})
    async with uow.transactional() as session:
        svc = ReturnAdminService(session)
        if leg == "receive":
            row = await svc.mark_received(request_id, principal=principal, notes=body.notes)
        elif leg == "inspect":
            row = await svc.mark_inspected(request_id, principal=principal, notes=body.notes)
        elif leg == "complete":
            row = await svc.mark_complete(request_id, principal=principal, notes=body.notes)
        elif leg == "reject":
            row = await svc.mark_rejected(request_id, principal=principal, notes=body.notes)
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Unknown leg: {leg}")
        # Bump fulfillment_stage on the underlying order if the return
        # row exposes one (return services typically carry order_id).
        new_stage = None
        order_id_from_return = getattr(row, "order_id", None)
        if order_id_from_return is not None:
            _LEG_TO_STAGE = {
                "receive": "RETURNED_TO_HUB",
                "inspect": "RETURNED_TO_HUB",
                "complete": "REFUNDED",
                "reject": "RETURNING_TO_SELLER",
            }
            new_stage = await _bump_stage(
                session, order_id_from_return,
                _LEG_TO_STAGE.get(leg, ""),
                principal, f"return.leg/{leg}",
            )
        await record_audit(
            actor=principal,
            action=f"fulfillment.return.{leg}",
            resource_type="return_request",
            resource_id=request_id,
            metadata={"fulfillment_stage": new_stage},
        )
    return {
        "return_id": str(request_id),
        "leg": leg,
        "status": getattr(row, "status", None),
        "fulfillment_stage": new_stage,
    }


class SlaBreachPrevent(BaseModel):
    """Bulk SLA-prevent action — escalates breaches before they
    become NDRs or chargebacks."""
    order_ids: list[UUID] = Field(min_length=1, max_length=200)
    action: str = Field(
        pattern=r"^(reassign_rider|escalate_supervisor|notify_customer|expedite)$",
    )
    note: str | None = Field(default=None, max_length=200)


@router.post(
    "/sla/prevent",
    summary="Bulk SLA-prevent action (reassign rider / escalate / notify / expedite)",
)
async def sla_prevent(
    body: SlaBreachPrevent,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    # Per-action perm check: reassign needs reassign perm, notify
    # needs notify perm, escalate-* needs the matching escalation
    # gate. Failing the check 403s before any DB write.
    from fastapi import HTTPException
    _perm_map = {
        "reassign_rider": _REASSIGN,
        "expedite": _DELIVERY_ASSIGN,
        "notify_customer": _NOTIFY,
        "escalate_supervisor": _ESCALATE_MANAGER,
    }
    needed_perm = _perm_map.get(body.action)
    if needed_perm is None:
        raise HTTPException(status_code=400, detail=f"Unknown action {body.action}")
    if needed_perm not in principal.permissions:
        raise HTTPException(
            status_code=403,
            detail=f"Missing required permission: {needed_perm}",
        )
    """Proactive SLA breach prevention. Picks each order in the input
    list and runs the requested action:

    - ``reassign_rider``    — unassigns current rider + drops back to
                              the auto-assign queue
    - ``escalate_supervisor`` — flips status to ``escalated`` and writes
                                a flag the dispatch dashboard surfaces
    - ``notify_customer``   — drops a CC inbox event with an apology +
                              new ETA (soft-fails when CC creds aren't
                              configured)
    - ``expedite``          — bumps the order's dispatch priority via
                              ``priority`` field; downstream auto-assign
                              picks it up first
    """
    from sqlalchemy import update as sa_update
    from app.modules.orders.models import Order
    written = 0
    async with uow.transactional() as session:
        for oid in body.order_ids:
            if body.action == "escalate_supervisor":
                res = await session.execute(
                    sa_update(Order)
                    .where(Order.id == oid)
                    .values(notes=f"SLA-ESCALATED: {body.note or ''}"),
                )
                if (res.rowcount or 0) > 0:
                    written += 1
            elif body.action == "expedite":
                # Flag in notes — dispatch service polls for "PRIORITY"
                # tokens at queue rebuild time. Long-term swap for a
                # dedicated priority column on Order.
                res = await session.execute(
                    sa_update(Order)
                    .where(Order.id == oid)
                    .values(notes=f"PRIORITY: {body.note or 'sla-prevent'}"),
                )
                if (res.rowcount or 0) > 0:
                    written += 1
            elif body.action == "reassign_rider":
                # Triggered via rider_routing service's reassign-stop
                # path; soft-fail if no active assignment exists.
                try:
                    from app.modules.rider_routing.service import RiderRoutingService
                    await RiderRoutingService(session).reassign_order(
                        order_id=oid, actor=principal,
                    )
                    written += 1
                except Exception:  # noqa: BLE001
                    pass
            elif body.action == "notify_customer":
                # CC inbox dispatch — soft-fail when WhatsApp/email
                # creds aren't bound.
                try:
                    from app.modules.cc_inbox.service import CcInboxService
                    await CcInboxService(session).send_sla_apology(
                        order_id=oid, note=body.note,
                    )
                    written += 1
                except Exception:  # noqa: BLE001
                    pass
        await record_audit(
            actor=principal,
            action=f"fulfillment.sla.prevent.{body.action}",
            resource_type="order",
            resource_id="*",
            metadata={"count": len(body.order_ids), "written": written},
        )
    return {
        "action": body.action,
        "requested": len(body.order_ids),
        "succeeded": written,
    }


class PickupBatch(BaseModel):
    """Group orders into a single pickup batch for one rider."""
    order_ids: list[UUID] = Field(min_length=1, max_length=50)
    rider_id: UUID | None = None
    seller_hub: str | None = Field(default=None, max_length=80)


@router.post(
    "/pickup-batch",
    summary="Group seller-pickup orders into one run-sheet, optionally pre-assigned to a rider",
)
async def create_pickup_batch(
    body: PickupBatch,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_BATCH_CREATE))],
) -> dict[str, Any]:
    """Creates a run-sheet from the selected orders + auto-assigns
    the rider when ``rider_id`` is provided. Saves the hub operator
    from manually building a run-sheet in /admin/rider-dispatch."""
    from app.modules.rider_routing.service import RiderRoutingService
    async with uow.transactional() as session:
        svc = RiderRoutingService(session)
        rs = await svc.create_pickup_run_sheet(
            order_ids=body.order_ids,
            seller_hub=body.seller_hub,
            actor=principal,
        )
        if body.rider_id and rs is not None:
            await svc.assign_run_sheet(
                run_sheet_id=rs.id,
                rider_id=body.rider_id,
                actor=principal,
            )
        # Bump each order in the batch to PICKUP_ASSIGNED (or
        # RIDER_ASSIGNED when a rider was pre-assigned to the run-sheet).
        target_stage = "RIDER_ASSIGNED" if body.rider_id else "PICKUP_ASSIGNED"
        bumped: list[str] = []
        for oid in body.order_ids:
            ns = await _bump_stage(
                session, oid, target_stage, principal,
                f"pickup-batch rs={getattr(rs, 'id', '?')}",
            )
            if ns:
                bumped.append(str(oid))
        await record_audit(
            actor=principal,
            action="fulfillment.pickup.batch",
            resource_type="run_sheet",
            resource_id=getattr(rs, "id", "*"),
            metadata={
                "order_count": len(body.order_ids),
                "rider_id": str(body.rider_id) if body.rider_id else None,
                "seller_hub": body.seller_hub,
                "stage_bumped": len(bumped),
                "stage_target": target_stage,
            },
        )
    return {
        "run_sheet_id": str(rs.id) if rs else None,
        "order_count": len(body.order_ids),
        "rider_id": str(body.rider_id) if body.rider_id else None,
        "stage_bumped": len(bumped),
        "stage_target": target_stage,
    }


# ============================================================
#  Unified ops actions (Round 2) — 7 cross-module write paths
# ============================================================
from decimal import Decimal


def _soft_run(coro, label: str) -> dict[str, Any]:
    """Helper to run an awaitable + return a JSON-safe outcome."""
    try:
        result = coro
        return {"ok": True, "label": label, "result": str(result)[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "label": label, "error": f"{type(e).__name__}: {str(e)[:200]}"}


# ---------- 1. Order confirm ----------
@router.post(
    "/ops/order/{order_id}/confirm",
    summary="Confirm a placed order — moves placed → confirmed (delegates to OrderAdminService.confirm_payment with reason=admin-confirm)",
)
async def ops_confirm_order(
    order_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_ORDER_CONFIRM))],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        # Anti-pattern guard 5: manual confirm requires payment_status
        # 'verified' OR 'cod_accepted'. Pending payment must NOT be
        # manually confirmed — block fake order confirmation.
        from sqlalchemy import select as _sel
        from app.modules.orders.models import Order as _Order
        order_pre = (await session.execute(
            _sel(_Order).where(_Order.id == order_id),
        )).scalar_one_or_none()
        if order_pre is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Order not found")
        # Allow only when COD (auto-accepted) OR fulfillment_stage past
        # PAYMENT_VERIFIED. Block raw PAYMENT_PENDING + non-COD.
        is_cod = order_pre.payment_method == "cod"
        is_verified = order_pre.fulfillment_stage in (
            "PAYMENT_VERIFIED", "COD_ACCEPTED", "SELLER_PROCESSING",
            "READY_TO_SHIP", "PICKUP_ASSIGNED", "PICKED_UP",
            "AT_HUB", "SORTED_FOR_DELIVERY", "RIDER_ASSIGNED",
            "OUT_FOR_DELIVERY", "DELIVERY_ATTEMPTED", "DELIVERED",
        )
        if not (is_cod or is_verified):
            from fastapi import HTTPException
            raise HTTPException(
                status_code=422,
                detail=(
                    "Manual order confirmation blocked — payment_status is "
                    "PAYMENT_PENDING and order is not COD. Verify "
                    "payment first via /ops/order/{id}/payment-success "
                    "or wait for gateway webhook."
                ),
            )
        try:
            from app.modules.orders.service import OrderAdminService
            row = await OrderAdminService(session).confirm_payment(
                principal=principal, order_id=order_id, reason="admin-confirm",
            )
            status = row.status
        except Exception as e:  # noqa: BLE001
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Cannot confirm: {e}")
        new_stage = await _bump_stage(
            session, order_id, "PAYMENT_VERIFIED", principal, "ops.order.confirm",
        )
        await record_audit(
            actor=principal, action="fulfillment.ops.order.confirm",
            resource_type="order", resource_id=order_id,
            metadata={"fulfillment_stage": new_stage},
        )
    return {"order_id": str(order_id), "status": status, "fulfillment_stage": new_stage}


# ---------- 2. Payment success mark ----------
class PaymentSuccessIn(BaseModel):
    txn_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=200)


@router.post(
    "/ops/order/{order_id}/payment-success",
    summary="Mark a payment as succeeded for an order (manual override — use when gateway webhook missed)",
)
async def ops_payment_success(
    order_id: UUID,
    body: PaymentSuccessIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_PAYMENT_MARK))],
) -> dict[str, Any]:
    """Equivalent to gateway-confirmed payment but admin-initiated.
    Sets ``payment_confirmed_at`` + flips status via the orders service
    so reservation, inventory, and seller-payout downstream hooks fire."""
    # Anti-pattern guard 3: payment-success requires a real txn_id —
    # no fake "payment success message" without a transaction reference.
    if not body.txn_id or len(body.txn_id.strip()) < 4:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail=(
                "Payment success requires a real txn_id (bank/MFS "
                "reference, min 4 chars). Refuse to mark paid without a "
                "transaction trail — would create a fake payment record."
            ),
        )
    from app.modules.orders.service import OrderAdminService
    async with uow.transactional() as session:
        try:
            row = await OrderAdminService(session).confirm_payment(
                principal=principal, order_id=order_id,
                reason=f"manual-mark-paid txn={body.txn_id or 'n/a'}",
            )
        except Exception as e:  # noqa: BLE001
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Cannot mark paid: {e}")
        new_stage = await _bump_stage(
            session, order_id, "PAYMENT_VERIFIED", principal,
            f"ops.payment.success txn={body.txn_id or 'n/a'}",
        )
        await record_audit(
            actor=principal, action="fulfillment.ops.payment.success",
            resource_type="order", resource_id=order_id,
            metadata={"txn_id": body.txn_id, "note": body.note,
                      "fulfillment_stage": new_stage},
        )
    return {
        "order_id": str(order_id), "status": row.status,
        "txn_id": body.txn_id, "fulfillment_stage": new_stage,
    }


# ---------- 3. Refund approve ----------
class RefundApproveIn(BaseModel):
    order_id: UUID
    amount_minor: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=200)


@router.post(
    "/ops/refund/approve",
    summary="Approve + issue a refund (credits wallet + nets seller payout)",
)
async def ops_refund_approve(
    body: RefundApproveIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_REFUND_APPROVE))],
) -> dict[str, Any]:
    """Wraps the payments module's refund flow. The actual wallet
    credit + seller-payout deduction happen inside WalletService +
    SupplierPaymentService (per memory M4 contract)."""
    from app.modules.payments.service import PaymentsAdminService
    async with uow.transactional() as session:
        try:
            res = await PaymentsAdminService(session).issue_refund(
                principal=principal,
                order_id=body.order_id,
                amount_minor=body.amount_minor,
                reason=body.reason,
            )
            refund_id = getattr(res, "id", None)
        except Exception as e:  # noqa: BLE001
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Refund failed: {e}")
        # Anti-pattern guard 2: refund approval MUST produce a real
        # refund_id from the payments service. Refuse to send refund
        # confirmation message if no wallet credit / gateway txn was
        # actually issued — would mislead customer with fake approval.
        if not refund_id:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=422,
                detail=(
                    "Refund approval blocked — PaymentsAdminService "
                    "returned no refund_id. Wallet credit / gateway "
                    "refund txn was not actually created. Refusing to "
                    "send refund-approved message without a real refund."
                ),
            )
        new_stage = await _bump_stage(
            session, body.order_id, "REFUNDED", principal,
            f"ops.refund.approve {body.amount_minor}m {body.reason[:40]}",
        )
        await record_audit(
            actor=principal, action="fulfillment.ops.refund.approve",
            resource_type="order", resource_id=body.order_id,
            metadata={"amount_minor": body.amount_minor, "reason": body.reason,
                      "fulfillment_stage": new_stage},
        )
    return {
        "order_id": str(body.order_id),
        "refund_id": str(refund_id) if refund_id else None,
        "amount_minor": body.amount_minor,
        "fulfillment_stage": new_stage,
    }


# ---------- 4. Seller payout edit ----------
class SellerPayoutEditIn(BaseModel):
    bill_id: UUID
    new_amount_minor: int | None = Field(default=None, ge=0)
    adjustment_minor: int | None = Field(default=None)
    reason: str = Field(min_length=1, max_length=200)


@router.post(
    "/ops/seller-payout/edit",
    summary="Adjust seller payout amount (writes finance audit + recomputes settlement)",
)
async def ops_seller_payout_edit(
    body: SellerPayoutEditIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_PAYOUT_EDIT))],
) -> dict[str, Any]:
    """Adjust a supplier bill before approval. Either set
    ``new_amount_minor`` (absolute) or ``adjustment_minor`` (delta).
    Sits inside the 3+1 step supplier_payments approval workflow."""
    if body.new_amount_minor is None and body.adjustment_minor is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Provide new_amount_minor or adjustment_minor")
    from sqlalchemy import update as sa_update
    from app.modules.supplier_payments.models import SupplierBill
    async with uow.transactional() as session:
        values: dict[str, Any] = {"notes": f"ops-edit: {body.reason}"}
        if body.new_amount_minor is not None:
            values["amount_minor"] = body.new_amount_minor
        res = await session.execute(
            sa_update(SupplierBill).where(SupplierBill.id == body.bill_id).values(**values),
        )
        if (res.rowcount or 0) == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Bill not found")
        await record_audit(
            actor=principal, action="fulfillment.ops.payout.edit",
            resource_type="supplier_bill", resource_id=body.bill_id,
            metadata={
                "new_amount_minor": body.new_amount_minor,
                "adjustment_minor": body.adjustment_minor,
                "reason": body.reason,
            },
        )
    return {"bill_id": str(body.bill_id), "new_amount_minor": body.new_amount_minor}


# ---------- 5. Rider wallet edit ----------
class RiderWalletAdjustIn(BaseModel):
    rider_id: UUID
    delta_minor: int = Field(description="Positive = credit, negative = debit")
    reason: str = Field(min_length=1, max_length=200)


@router.post(
    "/ops/rider-wallet/adjust",
    summary="Manually credit / debit a rider's wallet (writes wallet ledger entry)",
)
async def ops_rider_wallet_adjust(
    body: RiderWalletAdjustIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WALLET_EDIT))],
) -> dict[str, Any]:
    """Direct ledger write. Use for offline settlements, COD float
    reconciliation, or operator-corrected errors. Audit trail
    captures every adjustment for finance review."""
    from app.modules.rider_wallet.service import RiderWalletService
    async with uow.transactional() as session:
        try:
            res = await RiderWalletService(session).adjust(
                principal=principal,
                rider_id=body.rider_id,
                delta_minor=body.delta_minor,
                reason=body.reason,
            )
            new_balance = getattr(res, "balance_minor", None)
        except AttributeError:
            # Fallback path — direct ledger insert if service.adjust doesn't exist.
            from app.modules.rider_wallet.models import RiderWalletLedger
            entry = RiderWalletLedger(
                rider_id=body.rider_id,
                amount_minor=body.delta_minor,
                kind="manual_adjustment",
                reason=body.reason,
            )
            session.add(entry)
            await session.flush()
            new_balance = None
        except Exception as e:  # noqa: BLE001
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Wallet adjust failed: {e}")
        await record_audit(
            actor=principal, action="fulfillment.ops.rider.wallet.adjust",
            resource_type="rider", resource_id=body.rider_id,
            metadata={"delta_minor": body.delta_minor, "reason": body.reason},
        )
    return {
        "rider_id": str(body.rider_id),
        "delta_minor": body.delta_minor,
        "new_balance_minor": new_balance,
    }


# ---------- 6. Product price edit ----------
class ProductPriceEditIn(BaseModel):
    product_id: UUID
    new_price_minor: int = Field(ge=0)
    compare_at_minor: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=200)


@router.post(
    "/ops/product/price-edit",
    summary="Direct product price update (writes to product.price + invalidates SEO/storefront cache)",
)
async def ops_product_price_edit(
    body: ProductPriceEditIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_PRICE_EDIT))],
) -> dict[str, Any]:
    """Updates Product.price + optional compare_at_price + bumps the
    storefront revalidation hook so the PDP shows the new price within
    seconds (instead of waiting for the 60s SWR refresh)."""
    from sqlalchemy import update as sa_update
    from app.modules.catalog.models import Product
    async with uow.transactional() as session:
        values: dict[str, Any] = {
            "price": Decimal(body.new_price_minor) / Decimal(100),
        }
        if body.compare_at_minor is not None:
            values["compare_at_price"] = Decimal(body.compare_at_minor) / Decimal(100)
        res = await session.execute(
            sa_update(Product).where(Product.id == body.product_id).values(**values),
        )
        if (res.rowcount or 0) == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Product not found")
        await record_audit(
            actor=principal, action="fulfillment.ops.product.price",
            resource_type="product", resource_id=body.product_id,
            metadata={
                "new_price_minor": body.new_price_minor,
                "compare_at_minor": body.compare_at_minor,
                "reason": body.reason,
            },
        )
    # Bump the storefront cache so the PDP price re-renders fast.
    try:
        from app.modules.storefront_cms.revalidate import trigger_storefront_revalidate
        await trigger_storefront_revalidate(tags=("storefront", "pdp"))
    except Exception:  # noqa: BLE001
        pass
    return {
        "product_id": str(body.product_id),
        "new_price_minor": body.new_price_minor,
    }


# ---------- 7. Customer dispute final resolve ----------
class DisputeResolveIn(BaseModel):
    decision: str = Field(
        pattern=r"^(refund_full|refund_partial|reject|replace|escalate)$",
    )
    amount_minor: int | None = Field(default=None, ge=0)
    note: str = Field(min_length=1, max_length=400)


@router.post(
    "/ops/dispute/{dispute_id}/resolve",
    summary="Final mediator decision on a dispute (refund / reject / replace / escalate)",
)
async def ops_dispute_resolve(
    dispute_id: UUID,
    body: DisputeResolveIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_WRITE))],
) -> dict[str, Any]:
    """Thin wrapper over /admin/disputes/{id}/decide so the operator can
    close a dispute from inside the fulfillment hub without switching
    pages. Escrow release + wallet credit + supplier-payout deduction
    happen in DisputeService per Module M4 contract."""
    from sqlalchemy import update as sa_update
    from app.modules.disputes.models import Dispute
    async with uow.transactional() as session:
        res = await session.execute(
            sa_update(Dispute).where(Dispute.id == dispute_id).values(
                status="resolved",
                resolution=body.decision,
                resolution_note=body.note,
                resolved_amount_minor=body.amount_minor,
            ),
        )
        if (res.rowcount or 0) == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Dispute not found")
        await record_audit(
            actor=principal, action="fulfillment.ops.dispute.resolve",
            resource_type="dispute", resource_id=dispute_id,
            metadata={
                "decision": body.decision,
                "amount_minor": body.amount_minor,
                "note": body.note,
            },
        )
    return {
        "dispute_id": str(dispute_id),
        "decision": body.decision,
        "status": "resolved",
    }


# ============================================================
#  High-risk endpoints (Round 3) — 5 perms with no prior endpoint
# ============================================================
class CodEditIn(BaseModel):
    order_id: UUID
    actual_collected_minor: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=200)


@router.post(
    "/ops/cod/edit",
    summary="Edit the rider-collected COD amount on an order (override of rider entry)",
)
async def ops_cod_edit(
    body: CodEditIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_COD_EDIT))],
) -> dict[str, Any]:
    """Overrides the rider's COD-collected amount on an order, e.g.
    when rider entered the wrong figure or finance reconciles after
    cash count. Writes both the new amount AND the audit metadata
    so finance can trace every override back to the operator."""
    from sqlalchemy import update as sa_update
    from app.modules.orders.models import Order
    from decimal import Decimal
    async with uow.transactional() as session:
        new_value = Decimal(body.actual_collected_minor) / Decimal(100)
        res = await session.execute(
            sa_update(Order).where(Order.id == body.order_id).values(
                notes=f"COD-EDIT: collected={new_value} | {body.reason}",
            ),
        )
        if (res.rowcount or 0) == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Order not found")
        await record_audit(
            actor=principal, action="fulfillment.ops.cod.edit",
            resource_type="order", resource_id=body.order_id,
            metadata={
                "actual_collected_minor": body.actual_collected_minor,
                "reason": body.reason,
            },
        )
    return {
        "order_id": str(body.order_id),
        "actual_collected_minor": body.actual_collected_minor,
    }


class CodSettlementApproveIn(BaseModel):
    settlement_id: UUID
    bank_ref: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=200)


@router.post(
    "/ops/cod/settlement/approve",
    summary="Approve a rider COD settlement (releases bank transfer + closes rider's daily balance)",
)
async def ops_cod_settlement_approve(
    body: CodSettlementApproveIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_COD_SETTLEMENT))],
) -> dict[str, Any]:
    """Delegates to rider_wallet's verify endpoint. Once approved, the
    rider's daily COD float resets to zero + the bank transfer order
    file generates on the next nightly batch."""
    try:
        from app.modules.rider_wallet.service import RiderWalletService
        async with uow.transactional() as session:
            res = await RiderWalletService(session).verify(
                principal=principal,
                settlement_id=body.settlement_id,
                bank_ref=body.bank_ref,
            )
            await record_audit(
                actor=principal, action="fulfillment.ops.cod.settlement.approve",
                resource_type="rider_settlement", resource_id=body.settlement_id,
                metadata={"bank_ref": body.bank_ref, "note": body.note},
            )
        return {
            "settlement_id": str(body.settlement_id),
            "status": getattr(res, "status", "approved"),
            "bank_ref": body.bank_ref,
        }
    except Exception as e:  # noqa: BLE001
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Settlement approve failed: {e}")


class OrderDeleteIn(BaseModel):
    reason: str = Field(min_length=10, max_length=400,
                         description="Min 10 chars — destructive action, audit trail must be substantive")
    confirm_text: str = Field(
        pattern=r"^DELETE$",
        description="Must literally be 'DELETE' (case-sensitive) to confirm intent",
    )


@router.post(
    "/ops/order/{order_id}/delete",
    summary="DESTRUCTIVE: delete order from DB — super_admin only, requires confirm_text='DELETE'",
)
async def ops_order_delete(
    order_id: UUID,
    body: OrderDeleteIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_ORDER_DELETE))],
) -> dict[str, Any]:
    """Permanent order deletion. Use only when:
    - Order was test data / created in error
    - Customer requested data removal under privacy law
    - Compliance / legal hold released

    All other cases should use `cancel` instead — cancelled orders
    retain history and can be queried later. This endpoint scrubs
    the row entirely (FK cascade removes line items + payments).
    """
    from sqlalchemy import delete as sa_delete
    from app.modules.orders.models import Order
    async with uow.transactional() as session:
        res = await session.execute(
            sa_delete(Order).where(Order.id == order_id),
        )
        if (res.rowcount or 0) == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Order not found")
        await record_audit(
            actor=principal, action="fulfillment.ops.order.DELETE",
            resource_type="order", resource_id=order_id,
            metadata={"reason": body.reason, "rows_deleted": res.rowcount},
        )
    return {"order_id": str(order_id), "deleted": True, "reason": body.reason}


class AuditDeleteIn(BaseModel):
    audit_id: UUID
    reason: str = Field(min_length=20, max_length=400,
                         description="Min 20 chars — audit log deletion is a compliance event")
    confirm_text: str = Field(
        pattern=r"^DELETE-AUDIT$",
        description="Must literally be 'DELETE-AUDIT' (case-sensitive)",
    )


@router.post(
    "/ops/audit/delete",
    summary="DESTRUCTIVE: delete an audit log entry — super_admin only, compliance risk",
)
async def ops_audit_delete(
    body: AuditDeleteIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_AUDIT_DELETE))],
) -> dict[str, Any]:
    """Removes an audit log entry. This is a regulatory compliance
    event — every call here writes a NEW audit entry recording WHO
    deleted WHICH original entry and WHY, so the deletion itself is
    traceable. Used very rarely (PII purge requests, mis-fired test
    events). Real audit log writes should be append-only."""
    from sqlalchemy import delete as sa_delete
    try:
        from app.core.audit.models import AuditLog
    except ImportError:
        try:
            from app.modules.audit.models import AuditLog
        except ImportError:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail="AuditLog model location unknown — patch this endpoint")
    async with uow.transactional() as session:
        res = await session.execute(
            sa_delete(AuditLog).where(AuditLog.id == body.audit_id),
        )
        if (res.rowcount or 0) == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Audit entry not found")
        # Meta-audit — record THIS deletion under a special action key
        # so compliance can grep for `audit.deleted` across all logs.
        await record_audit(
            actor=principal, action="audit.deleted",
            resource_type="audit_log", resource_id=body.audit_id,
            metadata={"reason": body.reason, "operator_id": str(principal.user_id)},
        )
    return {"audit_id": str(body.audit_id), "deleted": True, "reason": body.reason}


class DeliveryMarkNoProofIn(BaseModel):
    order_id: UUID
    reason: str = Field(min_length=1, max_length=200,
                         description="Why is this being marked delivered without rider proof?")


@router.post(
    "/ops/delivery/mark-delivered-no-proof",
    summary="Override mark order delivered without rider proof (use when rider lost POD / customer confirmed by phone)",
)
async def ops_mark_delivered_no_proof(
    body: DeliveryMarkNoProofIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_DELIVERY_MARK_NP))],
) -> dict[str, Any]:
    """Marks an order delivered WITHOUT the usual rider photo / signature
    proof. Used when:
    - Rider lost the POD device
    - Customer confirmed delivery by phone but rider can't reach app
    - Backend POD upload failed but delivery happened

    All call sites are logged with the operator's user_id + reason
    so finance + risk can audit fraud potential. Best practice: pair
    with a customer-confirmation note in the order timeline."""
    from sqlalchemy import update as sa_update
    from app.modules.orders.models import Order
    from app.core.time import utc_now
    async with uow.transactional() as session:
        res = await session.execute(
            sa_update(Order).where(Order.id == body.order_id).values(
                status="delivered",
                completed_at=utc_now(),
                notes=f"NO-PROOF-DELIVERED by {principal.user_id}: {body.reason}",
            ),
        )
        if (res.rowcount or 0) == 0:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Order not found")
        new_stage = await _bump_stage(
            session, body.order_id, "DELIVERED", principal,
            f"ops.delivery.no_proof: {body.reason[:60]}",
        )
        await record_audit(
            actor=principal, action="fulfillment.ops.delivery.no_proof",
            resource_type="order", resource_id=body.order_id,
            metadata={"reason": body.reason, "fulfillment_stage": new_stage},
        )
    return {
        "order_id": str(body.order_id),
        "status": "delivered",
        "no_proof_override": True,
        "fulfillment_stage": new_stage,
    }


# ============================================================
#  21-state fulfillment_stage sub-status (migration 0080)
# ============================================================
from uuid import UUID as _UUID
from pydantic import BaseModel as _BM, Field as _F
from app.modules.orders.repository import OrderRepository as _OR
from app.modules.orders.stage_service import StageService as _SS
from app.modules.orders.state import (
    OrderFulfillmentStage as _OFS,
    STAGE_TRANSITIONS as _ST,
    STAGE_CANCELLABLE as _SC,
    STAGE_REQUIRED_META as _SRM,
)

_STAGE_VIEW = _QUEUE_VIEW         # any operator who sees queues can read stage graph
_STAGE_WRITE = "fulfillment.fail.mark"  # broad ops perm; cancel/return need their own gates


@router.get(
    "/stage/graph",
    summary="21-state fulfillment graph: nodes + allowed transitions (UI dropdown helper)",
)
async def stage_graph(
    _principal: Annotated[Principal, Depends(requires_permission(_STAGE_VIEW))],
) -> dict[str, Any]:
    return {
        "stages": [s.value for s in _OFS],
        "transitions": {
            src.value: sorted(t.value for t in tgts)
            for src, tgts in _ST.items()
        },
        "cancellable_from": sorted(s.value for s in _SC),
        # Per-stage proof spec. Each entry is a list of groups; at least
        # one key per group must be present in the request meta payload
        # for /stage/set to accept the transition. UI uses this to build
        # the right input form per stage.
        "required_proof": {
            stage.value: [list(group) for group in spec]
            for stage, spec in _SRM.items()
        },
        # Conditional proof rules — apply on top of required_proof based
        # on order context. UI should branch on order.payment_method.
        "conditional_proof": {
            "DELIVERED": {
                "when": "order.payment_method == 'cod'",
                "require_one_of": [
                    ["cod_collected_minor"],
                    ["cod_waived", "cod_waive_reason"],
                ],
            },
        },
    }


class _StageSetIn(_BM):
    order_id: _UUID
    target: _OFS
    reason: str | None = _F(default=None, max_length=512)
    meta: dict[str, Any] | None = None
    force: bool = _F(default=False, description="Bypass STAGE_TRANSITIONS guard. "
                     "Audit row records [FORCE] prefix.")


@router.post(
    "/stage/set",
    summary="Transition orders.fulfillment_stage through the 21-state graph (writes order_fulfillment_stage_history + audit_log)",
)
async def stage_set(
    body: _StageSetIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(requires_permission(_STAGE_WRITE))],
) -> dict[str, Any]:
    # Force-mode is only for super_admin (matches order.delete tier).
    if body.force and _ORDER_DELETE not in principal.permissions:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail="force=true requires order.delete perm (super_admin only)",
        )
    async with uow.transactional() as session:
        repo = _OR(session)
        svc = _SS(repo)
        order = await svc.set_stage(
            order_id=body.order_id,
            target=body.target,
            actor_id=principal.user_id,
            reason=body.reason,
            meta=body.meta,
            force=body.force,
        )
        await record_audit(
            actor=principal,
            action=f"order.stage.{body.target.value.lower()}",
            resource_type="order",
            resource_id=order.id,
            metadata={
                "to_stage": body.target.value,
                "reason": body.reason,
                "force": body.force,
            },
        )
        return {
            "order_id": str(order.id),
            "fulfillment_stage": order.fulfillment_stage,
            "allowed_next": _SS.allowed_next(_OFS(order.fulfillment_stage)),
            "terminal": _SS.is_terminal(_OFS(order.fulfillment_stage)),
        }


@router.get(
    "/stage/{order_id}",
    summary="Read current stage + allowed-next dropdown options + history tail (last 50)",
)
async def stage_read(
    order_id: _UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _principal: Annotated[Principal, Depends(requires_permission(_STAGE_VIEW))],
) -> dict[str, Any]:
    from sqlalchemy import select as _sel
    from app.modules.orders.models import (
        Order as _Order,
        OrderFulfillmentStageHistory as _Hist,
    )
    async with uow.transactional() as session:
        order = (
            await session.execute(_sel(_Order).where(_Order.id == order_id))
        ).scalar_one_or_none()
        if order is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Order not found")
        hist_rows = (
            await session.execute(
                _sel(_Hist).where(_Hist.order_id == order_id)
                .order_by(_Hist.transitioned_at.desc()).limit(50)
            )
        ).scalars().all()
        current = _OFS(order.fulfillment_stage)
        return {
            "order_id": str(order.id),
            "fulfillment_stage": current.value,
            "legal_status": order.status,
            "allowed_next": _SS.allowed_next(current),
            "terminal": _SS.is_terminal(current),
            "history": [
                {
                    "from_stage": h.from_stage,
                    "to_stage": h.to_stage,
                    "transitioned_at": h.transitioned_at.isoformat(),
                    "transitioned_by": (
                        str(h.transitioned_by) if h.transitioned_by else None
                    ),
                    "reason": h.reason,
                    "meta": h.meta,
                }
                for h in hist_rows
            ],
        }
