"""Order-keyed stock orchestrator.

This is the surface the orders/payments modules call into. Each method takes
an ``order_id`` and uses it as the ledger ``correlation_id`` so the operation
is idempotent on retry — critical because payment-success callbacks and
delivery webhooks are at-least-once delivery from external systems.

Lifecycle (per the project's reservation rules)
----------------------------------------------
1. Customer places order → no stock impact.
2. Payment succeeds → :meth:`reserve_for_order` (FEFO across batches, locks
   balance rows, refuses if insufficient).
3. Order cancelled / refunded → :meth:`release_for_order`.
4. Delivery completed → :meth:`consume_for_order` (drains ``reserved`` so it
   leaves the system).

Concurrency
-----------
Each method runs inside its caller's ``UnitOfWork.transactional()`` scope.
Concurrent calls for the *same* (variant, batch, warehouse) serialize on
``SELECT ... FOR UPDATE`` of the matching ``stock_balances`` row. The
``CHECK quantity >= 0`` constraint on ``stock_balances`` is the last-line
guarantee that overselling cannot escape a service-layer bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.config import get_settings
from app.core.errors import ConflictError, ValidationError
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.modules.inventory.models import LedgerKind, StockBucket
from app.modules.inventory.service import InventoryService, ReservedAllocation

_logger = get_logger("hypershop.inventory.orchestrator")


@dataclass(frozen=True, slots=True)
class OrderItem:
    variant_id: UUID
    quantity: int


@dataclass(frozen=True, slots=True)
class OrderReservationResult:
    order_id: UUID
    items: list[ReservedAllocation]
    already_reserved: bool


class OrderStockOrchestrator:
    """Single seam for the orders/payments modules.

    All three methods are idempotent on ``order_id``: calling
    :meth:`reserve_for_order` twice for the same order returns the existing
    reservation; :meth:`release_for_order` is safe to retry; second
    :meth:`consume_for_order` for the same order is a no-op.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.inventory = InventoryService(session)

    async def reserve_for_order(
        self,
        *,
        principal: Principal | SystemPrincipal,
        order_id: UUID,
        items: list[OrderItem],
        warehouse_code: str | None = None,
    ) -> OrderReservationResult:
        """Reserve every line of an order. **All-or-nothing**: if any single
        line cannot be fulfilled the whole transaction rolls back, so partial
        order reservations never get committed.
        """
        if not items:
            raise ValidationError("Order has no lines to reserve.")

        cfg = get_settings()
        wh_code = warehouse_code or cfg.inventory_default_warehouse_code

        # Idempotency: if anything already reserved under this order_id, return
        # what's there. We don't try to "top up" a partially-reserved order
        # because the orders module owns that decision (it knows whether it's
        # a retry or an amendment).
        existing = await self.inventory._existing_reserve_allocations(order_id)
        if existing:
            await record_audit(
                actor=principal,
                action="inventory.order.reserve.idempotent",
                resource_type="order",
                resource_id=order_id,
            )
            return OrderReservationResult(
                order_id=order_id, items=existing, already_reserved=True,
            )

        all_allocations: list[ReservedAllocation] = []
        for item in items:
            if item.quantity <= 0:
                raise ValidationError(
                    f"Item quantity must be positive (variant {item.variant_id}).",
                )
            _, allocs = await self.inventory.reserve_stock(
                principal=principal,
                variant_id=item.variant_id,
                warehouse_code=wh_code,
                quantity=item.quantity,
                reference_type="order",
                reference_id=order_id,
                correlation_id=order_id,
            )
            all_allocations.extend(allocs)

        await record_audit(
            actor=principal,
            action="inventory.order.reserve",
            resource_type="order",
            resource_id=order_id,
            metadata={
                "items": [
                    {"variant_id": str(i.variant_id), "quantity": i.quantity}
                    for i in items
                ],
                "warehouse_code": wh_code,
            },
        )
        return OrderReservationResult(
            order_id=order_id, items=all_allocations, already_reserved=False,
        )

    async def release_for_order(
        self,
        *,
        principal: Principal | SystemPrincipal,
        order_id: UUID,
        notes: str | None = None,
    ) -> int:
        """Release every reservation booked under ``order_id``.

        Idempotent: if nothing is reserved (already released, or never
        reserved), returns 0.
        """
        actor_id = principal.user_id if isinstance(principal, Principal) else None
        existing = await self.inventory._existing_reserve_allocations(order_id)
        if not existing:
            await record_audit(
                actor=principal,
                action="inventory.order.release.noop",
                resource_type="order",
                resource_id=order_id,
            )
            return 0

        # Re-check that there's actually anything *currently* reserved (could
        # have already been consumed). _existing_reserve_allocations counts
        # the +reserved legs; if those have been consumed, the RESERVED
        # balance row may already be zero. We still emit RELEASE on the
        # remaining quantity.
        released = 0
        rows = await self.inventory._reserve_rows(order_id)
        for row in rows:
            if row.bucket != StockBucket.RESERVED or row.quantity_delta <= 0:
                continue
            locked = await self.inventory.stock.get_balance_locked(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.RESERVED,
            )
            if locked is None or locked.quantity <= 0:
                continue
            give_back = min(row.quantity_delta, locked.quantity)
            await self.inventory.stock.apply_movement(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.RESERVED,
                quantity_delta=-give_back,
                kind=LedgerKind.RELEASE,
                actor_id=actor_id,
                correlation_id=order_id,
                reference_type="order",
                reference_id=order_id,
                notes=notes,
            )
            await self.inventory.stock.apply_movement(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.AVAILABLE,
                quantity_delta=give_back,
                kind=LedgerKind.RELEASE,
                actor_id=actor_id,
                correlation_id=order_id,
                reference_type="order",
                reference_id=order_id,
                notes=notes,
            )
            released += give_back

        await record_audit(
            actor=principal,
            action="inventory.order.release",
            resource_type="order",
            resource_id=order_id,
            metadata={"released": released},
        )
        return released

    async def consume_for_order(
        self,
        *,
        principal: Principal | SystemPrincipal,
        order_id: UUID,
        notes: str | None = None,
    ) -> int:
        """Consume the entire reserved stock under ``order_id``.

        Use on delivery completion. Idempotent: re-running on a
        fully-consumed order is a no-op.
        """
        actor_id = principal.user_id if isinstance(principal, Principal) else None
        rows = [
            r
            for r in await self.inventory._reserve_rows(order_id)
            if r.bucket == StockBucket.RESERVED and r.quantity_delta > 0
        ]
        if not rows:
            return 0

        consumed = 0
        for row in rows:
            locked = await self.inventory.stock.get_balance_locked(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.RESERVED,
            )
            if locked is None or locked.quantity <= 0:
                continue
            take = min(row.quantity_delta, locked.quantity)
            await self.inventory.stock.apply_movement(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.RESERVED,
                quantity_delta=-take,
                kind=LedgerKind.CONSUME,
                actor_id=actor_id,
                correlation_id=order_id,
                reference_type="order",
                reference_id=order_id,
                notes=notes,
            )
            consumed += take

        await record_audit(
            actor=principal,
            action="inventory.order.consume",
            resource_type="order",
            resource_id=order_id,
            metadata={"consumed": consumed},
        )
        return consumed


__all__ = [
    "OrderItem",
    "OrderReservationResult",
    "OrderStockOrchestrator",
]


def _check_unused_conflict_import() -> None:  # pragma: no cover
    """ConflictError is part of the contract — re-exported via service for
    API layers that want to catch insufficient-stock specifically."""
    _ = ConflictError
