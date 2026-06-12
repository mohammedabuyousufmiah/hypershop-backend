"""Orders service — owns the state machine and emits the events that drive
inventory + notifications.

Lifecycle entry points
----------------------
- :meth:`place_order` — checkout. Snapshots product/variant data, computes
  totals, creates header + lines + initial history. For COD, immediately
  transitions to ``payment_confirmed`` and triggers reservation. For online,
  starts in ``pending_payment``.
- :meth:`confirm_payment` — admin/payments hook. Transitions
  ``pending_payment`` → ``payment_confirmed`` and triggers reservation
  inline (so we get an atomic "paid + reserved" or fail both).
- :meth:`start_packing`, :meth:`dispatch`,
  :meth:`complete` — staff endpoints, each guarded by the state machine.
- :meth:`cancel_by_customer`, :meth:`cancel_by_admin` — both write a
  CANCELLED transition and emit ``orders.order.cancelled`` (inventory
  releases via its handler).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.events.outbox import enqueue_outbox
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.catalog.models import Product, ProductStatus, ProductVariant
from app.modules.inventory.orchestrator import OrderItem, OrderStockOrchestrator
from app.modules.orders.codes import make_order_code
from app.modules.orders.events import (
    EVT_ORDER_APPROVED,
    EVT_ORDER_CANCELLED,
    EVT_ORDER_COMPLETED,
    EVT_ORDER_CREATED,
    EVT_ORDER_DISPATCHED,
    EVT_ORDER_PACKING_STARTED,
    EVT_ORDER_PAYMENT_CONFIRMED,
    EVT_ORDER_RESERVATION_FAILED,
    EVT_ORDER_STOCK_RESERVED,
)
from app.modules.orders.models import Order, OrderLine, PaymentMethod
from app.modules.orders.repository import OrderRepository, require_order
from app.modules.orders.state import (
    CUSTOMER_CANCELLABLE_STATES,
    OrderStatus,
    TransitionError,
    assert_can_transition,
)

_logger = get_logger("hypershop.orders")
_CODE_RETRIES = 5


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = OrderRepository(session)

    # ---------------- Place ----------------

    async def place_order(
        self,
        *,
        principal: Principal,
        items: list[dict[str, Any]],
        payment_method: str,
        delivery_address: dict[str, Any],
        notes: str | None,
        currency: str,
    ) -> Order:
        if not items:
            raise ValidationError("Order has no items.")

        # Snapshot variants. Refuse if any variant is missing/inactive/blocked.
        snapshots = await self._snapshot_variants(
            [(item["variant_id"], item["quantity"]) for item in items],
        )

        # Compute totals.
        subtotal = sum(s["line_total"] for s in snapshots)

        # Resolve delivery fee from the address using the delivery
        # zones table (Module 27). Quoting failures are surfaced as
        # ValidationError so the customer sees "we can't deliver here"
        # instead of an opaque 500. COD/online surcharge kept in the
        # quote DTO for forward-compat (currently 0).
        from app.modules.delivery.service import DeliveryService

        delivery_quote = await DeliveryService(self.session).quote(
            city=str(delivery_address.get("city", "")),
            postal_code=str(delivery_address.get("postal_code") or "") or None,
            payment_method=payment_method,
        )
        if delivery_quote.currency.upper() != currency.upper():
            raise ValidationError(
                f"Delivery zone currency {delivery_quote.currency} does not "
                f"match order currency {currency}.",
                details={"zone_code": delivery_quote.zone_code},
            )
        shipping_total = delivery_quote.total
        grand_total = subtotal + shipping_total

        # Hypershop e-commerce marketplace: prescription-gating is
        # not part of the public storefront. The catalog still carries
        # a `requires_prescription` flag for backward compat but it
        # never blocks an order — any_rx is computed for telemetry only.
        any_rx = any(s.get("requires_prescription", False) for s in snapshots)
        method = PaymentMethod(payment_method)

        # Initial status — COD commits immediately, online waits.
        initial_status = (
            OrderStatus.PENDING_PAYMENT
            if method == PaymentMethod.ONLINE
            else OrderStatus.PAYMENT_CONFIRMED
        )

        code = await self._allocate_code()
        order = await self.repo.create(
            code=code,
            customer_user_id=principal.user_id,
            status=initial_status.value,
            payment_method=method.value,
            requires_prescription=any_rx,
            currency=currency.upper(),
            subtotal=subtotal,
            shipping_total=shipping_total,
            grand_total=grand_total,
            delivery_address=delivery_address,
            notes=notes,
            placed_at=utc_now(),
            payment_confirmed_at=(
                utc_now() if initial_status == OrderStatus.PAYMENT_CONFIRMED else None
            ),
        )
        for snap in snapshots:
            await self.repo.add_line(
                order_id=order.id,
                variant_id=snap["variant_id"],
                product_name=snap["product_name"],
                variant_sku=snap["variant_sku"],
                requires_prescription=snap["requires_prescription"],
                quantity=snap["quantity"],
                unit_price=snap["unit_price"],
                line_total=snap["line_total"],
            )
        # Refresh so the ``order.lines`` collection reflects the inserted
        # rows. Without this, ``_post_payment_confirmed_work`` would see an
        # empty collection and build an empty items list.
        await self.session.refresh(order, attribute_names=("lines",))
        await self.repo.add_history(
            order_id=order.id,
            from_status=None,
            to_status=initial_status.value,
            transitioned_by=principal.user_id,
            reason="placed",
        )

        await record_audit(
            actor=principal,
            action="orders.order.create",
            resource_type="order",
            resource_id=order.id,
            metadata={
                "code": code,
                "payment_method": method.value,
                "grand_total": str(grand_total),
                "requires_prescription": any_rx,
                "line_count": len(snapshots),
            },
        )
        await enqueue_outbox(
            type=EVT_ORDER_CREATED,
            payload={
                "order_id": str(order.id),
                "code": code,
                "customer_user_id": str(principal.user_id),
                "payment_method": method.value,
                "grand_total": str(grand_total),
                "currency": currency.upper(),
            },
        )

        # COD path: order is already in payment_confirmed. Emit the event
        # explicitly (the transition row was the initial placement history)
        # and run the post-confirm work (reservation + routing).
        if initial_status == OrderStatus.PAYMENT_CONFIRMED:
            await enqueue_outbox(
                type=EVT_ORDER_PAYMENT_CONFIRMED,
                payload={
                    "order_id": str(order.id),
                    "code": order.code,
                    "customer_user_id": str(principal.user_id),
                    "from_status": None,
                    "to_status": OrderStatus.PAYMENT_CONFIRMED.value,
                    "items": [
                        {"variant_id": str(s["variant_id"]), "quantity": s["quantity"]}
                        for s in snapshots
                    ],
                },
            )
            await self._post_payment_confirmed_work(order=order, principal=principal)
        # Cart-recovery attribution — best-effort, never blocks placement.
        try:
            from app.modules.cart_recovery.service import attribute_recovery
            await attribute_recovery(
                self.session,
                customer_user_id=order.customer_user_id,
                order_id=order.id,
                ordered_at=order.placed_at,
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger("hypershop.orders.service").warning(
                "cart_recovery_attribution_skipped order=%s", order.id,
                exc_info=True,
            )
        return await self._reload(order.id)

    async def _snapshot_variants(
        self, items: list[tuple[UUID, int]],
    ) -> list[dict[str, Any]]:
        from sqlalchemy import select

        # Aggregate quantities by variant — duplicate variant_ids in cart get merged.
        merged: dict[UUID, int] = {}
        for variant_id, qty in items:
            if qty <= 0:
                raise ValidationError("Item quantity must be positive.")
            merged[variant_id] = merged.get(variant_id, 0) + qty

        if not merged:
            raise ValidationError("Order has no items.")

        stmt = (
            select(ProductVariant, Product)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(ProductVariant.id.in_(merged.keys()))
        )
        rows = (await self.session.execute(stmt)).all()
        by_id = {variant.id: (variant, product) for (variant, product) in rows}

        snapshots: list[dict[str, Any]] = []
        for variant_id, qty in merged.items():
            pair = by_id.get(variant_id)
            if pair is None:
                raise NotFoundError(
                    f"Variant {variant_id} not found.",
                    details={"variant_id": str(variant_id)},
                )
            variant, product = pair
            if not variant.is_active:
                raise BusinessRuleError(
                    "Variant is not active.",
                    details={"variant_id": str(variant_id)},
                )
            if product.status != ProductStatus.ACTIVE:
                raise BusinessRuleError(
                    "Product is not available for purchase.",
                    details={"variant_id": str(variant_id), "status": product.status},
                )
            if product.blocked_at is not None:
                raise BusinessRuleError(
                    "Product is blocked.",
                    details={"variant_id": str(variant_id)},
                )
            if product.expires_at is not None and product.expires_at <= utc_now():
                raise BusinessRuleError(
                    "Product registration has expired.",
                    details={"variant_id": str(variant_id)},
                )
            snapshots.append(
                {
                    "variant_id": variant_id,
                    "product_name": product.name,
                    "variant_sku": variant.sku,
                    "requires_prescription": product.requires_prescription,
                    "quantity": qty,
                    "unit_price": variant.price,
                    "line_total": variant.price * Decimal(qty),
                },
            )
        return snapshots

    async def _allocate_code(self) -> str:
        for _ in range(_CODE_RETRIES):
            candidate = make_order_code()
            if not await self.repo.code_exists(candidate):
                return candidate
        raise BusinessRuleError("Could not allocate a unique order code.")

    # ---------------- Internal: post-payment-confirmed pipeline ----------------

    async def _post_payment_confirmed_work(
        self,
        *,
        order: Order,
        principal: Principal,
    ) -> None:
        """Run the part of the lifecycle that follows payment confirmation:
        reserve stock (with savepoint rollback to FAILED on shortage), then
        auto-approve. Hypershop is a pure e-commerce marketplace — no
        prescription gate.

        Caller must have already (a) put the order into PAYMENT_CONFIRMED
        and (b) emitted ``EVT_ORDER_PAYMENT_CONFIRMED``.
        """
        items = [
            OrderItem(variant_id=line.variant_id, quantity=line.quantity)
            for line in order.lines
        ]
        reservation_failed = False
        failure_reason: str | None = None

        uow = UnitOfWork()
        try:
            async with uow.transactional() as inner_session:
                _ = inner_session  # share via current_session()
                orchestrator = OrderStockOrchestrator(self.session)
                await orchestrator.reserve_for_order(
                    principal=principal,
                    order_id=order.id,
                    items=items,
                )
        except ConflictError as e:
            reservation_failed = True
            failure_reason = str(e)

        if reservation_failed:
            await self._transition(
                order=order,
                target=OrderStatus.FAILED,
                principal=principal,
                reason=failure_reason or "stock reservation failed",
                event_type=EVT_ORDER_RESERVATION_FAILED,
            )
            return

        await self._transition(
            order=order,
            target=OrderStatus.STOCK_RESERVED,
            principal=principal,
            reason="inventory reserved",
            event_type=EVT_ORDER_STOCK_RESERVED,
        )

        # Auto-approve every paid order — no prescription gate.
        await self._transition(
            order=order,
            target=OrderStatus.APPROVED,
            principal=principal,
            reason="auto-approved (e-commerce marketplace)",
            event_type=EVT_ORDER_APPROVED,
            timestamp_field="approved_at",
        )

    # ---------------- Status transitions (admin / staff endpoints) ----------------

    async def confirm_payment(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        reason: str | None,
    ) -> Order:
        """Online-payment confirmation entry point. The payments module (or
        admin) calls this. Triggers reservation inline.
        """
        order = require_order(await self.repo.get_locked(order_id))
        if order.payment_method != PaymentMethod.ONLINE.value:
            raise BusinessRuleError(
                "confirm_payment is only valid for online payment orders.",
                details={"payment_method": order.payment_method},
            )
        if OrderStatus(order.status) != OrderStatus.PENDING_PAYMENT:
            raise BusinessRuleError(
                f"Cannot confirm payment from status {order.status!r}.",
                details={"status": order.status},
            )
        # Transition to PAYMENT_CONFIRMED and emit the event before running
        # the post-confirm work. The payment_confirmed event payload
        # includes ``items`` so notifications can render the order summary
        # without re-fetching.
        await self._transition(
            order=order,
            target=OrderStatus.PAYMENT_CONFIRMED,
            principal=principal,
            reason=reason or "payment confirmed",
            event_type=EVT_ORDER_PAYMENT_CONFIRMED,
            timestamp_field="payment_confirmed_at",
        )
        await self._post_payment_confirmed_work(order=order, principal=principal)
        return await self._reload(order_id)


    async def start_packing(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        reason: str | None,
    ) -> Order:
        order = require_order(await self.repo.get_locked(order_id))
        await self._transition(
            order=order,
            target=OrderStatus.PACKING,
            principal=principal,
            reason=reason or "packing started",
            event_type=EVT_ORDER_PACKING_STARTED,
        )
        return await self._reload(order_id)

    async def dispatch(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        reason: str | None,
    ) -> Order:
        order = require_order(await self.repo.get_locked(order_id))
        await self._transition(
            order=order,
            target=OrderStatus.OUT_FOR_DELIVERY,
            principal=principal,
            reason=reason or "dispatched to rider",
            event_type=EVT_ORDER_DISPATCHED,
            timestamp_field="dispatched_at",
        )
        return await self._reload(order_id)

    async def complete(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        reason: str | None,
    ) -> Order:
        order = require_order(await self.repo.get_locked(order_id))
        await self._transition(
            order=order,
            target=OrderStatus.COMPLETED,
            principal=principal,
            reason=reason or "delivered to customer",
            event_type=EVT_ORDER_COMPLETED,
            timestamp_field="completed_at",
        )
        return await self._reload(order_id)

    # ---------------- Cancellation ----------------

    async def cancel_by_customer(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        reason: str,
    ) -> Order:
        order = require_order(await self.repo.get_locked(order_id))
        if order.customer_user_id != principal.user_id:
            raise ForbiddenError("This order does not belong to you.")
        if OrderStatus(order.status) not in CUSTOMER_CANCELLABLE_STATES:
            raise BusinessRuleError(
                "Order can no longer be cancelled by the customer.",
                details={"status": order.status},
            )
        await self._do_cancel(order=order, principal=principal, reason=reason)
        return await self._reload(order_id)

    async def cancel_by_admin(
        self,
        *,
        principal: Principal,
        order_id: UUID,
        reason: str,
    ) -> Order:
        order = require_order(await self.repo.get_locked(order_id))
        await self._do_cancel(order=order, principal=principal, reason=reason)
        return await self._reload(order_id)

    async def _do_cancel(
        self, *, order: Order, principal: Principal, reason: str,
    ) -> None:
        await self._transition(
            order=order,
            target=OrderStatus.CANCELLED,
            principal=principal,
            reason=reason,
            event_type=EVT_ORDER_CANCELLED,
            timestamp_field="cancelled_at",
            extra_payload={"reason": reason},
        )
        order.cancellation_reason = reason

    # ---------------- Read ----------------

    async def get_for_customer(
        self, *, principal: Principal, order_id: UUID,
    ) -> Order:
        order = require_order(await self.repo.get(order_id))
        if order.customer_user_id != principal.user_id:
            raise ForbiddenError("This order does not belong to you.")
        return order

    async def get_admin(self, order_id: UUID) -> Order:
        return require_order(await self.repo.get(order_id))

    # ---------------- Internal: transition + emit ----------------

    async def _transition(
        self,
        *,
        order: Order,
        target: OrderStatus,
        principal: Principal | SystemPrincipal,
        reason: str,
        event_type: str,
        timestamp_field: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        current = OrderStatus(order.status)
        if current == target:
            # Idempotent — caller is re-applying. No transition row, no event.
            return
        try:
            assert_can_transition(current, target)
        except TransitionError as e:
            raise BusinessRuleError(str(e), details={"from": e.current.value, "to": e.target.value})

        order.status = target.value
        if timestamp_field is not None:
            setattr(order, timestamp_field, utc_now())

        actor_id = principal.user_id if isinstance(principal, Principal) else None
        await self.repo.add_history(
            order_id=order.id,
            from_status=current.value,
            to_status=target.value,
            transitioned_by=actor_id,
            reason=reason,
        )
        await record_audit(
            actor=principal,
            action=f"orders.order.transition.{target.value}",
            resource_type="order",
            resource_id=order.id,
            metadata={"from": current.value, "to": target.value, "reason": reason},
        )
        payload: dict[str, Any] = {
            "order_id": str(order.id),
            "code": order.code,
            "customer_user_id": str(order.customer_user_id),
            "from_status": current.value,
            "to_status": target.value,
        }
        if extra_payload:
            payload.update(extra_payload)
        # Inventory reservation hook: when payment is confirmed, the inventory
        # handler reserves stock (idempotent — orchestrator may have already
        # done it inline). Cancellation hook releases. Completion hook consumes.
        if target == OrderStatus.PAYMENT_CONFIRMED:
            payload["items"] = [
                {"variant_id": str(line.variant_id), "quantity": line.quantity}
                for line in order.lines
            ]
        await enqueue_outbox(type=event_type, payload=payload)

    async def _reload(self, order_id: UUID) -> Order:
        order = await self.repo.get(order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        return order
