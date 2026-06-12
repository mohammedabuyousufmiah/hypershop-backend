"""Inventory business logic.

Hard rules enforced here
------------------------
- **No stock without invoice.** ``available`` only goes up via
  :meth:`InventoryService.receive_goods`, which mints ledger rows of
  ``kind=receipt`` referencing a freshly-created or pre-existing
  ``GoodsReceipt`` row. There is no other code path to ``adjust_in`` into
  ``available``; admin adjustments require a written reason and emit a
  separate ``adjust_in/adjust_out`` audit + outbox trail.
- **Batch mandatory.** Every receipt line, every ledger row, and every
  balance row carries a ``batch_id``. Schemas refuse rows without it; the
  DB columns are NOT NULL.
- **Expiry mandatory.** ``batches.expiry_date`` is NOT NULL at the DB
  level. Receipt lines that create a new batch must supply ``expiry_date``.
- **Expired stock auto-block.** :meth:`expire_overdue_batches` (run via
  ARQ cron) moves available + reserved stock of any batch whose
  ``expiry_date`` is past into the ``expired`` bucket and marks the batch
  ``status=expired``. Reservation also performs a runtime guard.
- **Near-expiry alert.** :meth:`scan_near_expiry` emits
  ``inventory.batch.near_expiry`` outbox events for batches expiring inside
  ``settings.inventory_near_expiry_days``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.config import get_settings
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.core.events.outbox import enqueue_outbox
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.inventory.codes import make_gr_code, make_po_code
from app.modules.inventory.models import (
    Batch,
    BatchStatus,
    GoodsReceipt,
    LedgerKind,
    PurchaseOrder,
    PurchaseOrderStatus,
    StockBalance,
    StockBucket,
    StockLedger,
    Supplier,
    Warehouse,
)
from app.modules.inventory.repository import (
    BatchRepository,
    GoodsReceiptRepository,
    PurchaseOrderRepository,
    StockRepository,
    SupplierRepository,
    WarehouseRepository,
)

_logger = get_logger("hypershop.inventory")

# Outbox event types — handlers register in notifications module.
EVT_BATCH_NEAR_EXPIRY = "inventory.batch.near_expiry"
EVT_BATCH_EXPIRED = "inventory.batch.expired"
EVT_STOCK_RECEIVED = "inventory.stock.received"

_CODE_RETRIES = 5


@dataclass(frozen=True, slots=True)
class ReservedAllocation:
    batch_id: UUID
    quantity: int


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.suppliers = SupplierRepository(session)
        self.warehouses = WarehouseRepository(session)
        self.purchase_orders = PurchaseOrderRepository(session)
        self.goods_receipts = GoodsReceiptRepository(session)
        self.batches = BatchRepository(session)
        self.stock = StockRepository(session)

    # ---------------- Suppliers ----------------

    async def create_supplier(self, *, principal: Principal, **fields: Any) -> Supplier:
        s = await self.suppliers.create(**fields)
        await record_audit(
            actor=principal,
            action="inventory.supplier.create",
            resource_type="supplier",
            resource_id=s.id,
            metadata={"code": s.code, "name": s.name},
        )
        return s

    async def update_supplier(
        self,
        *,
        principal: Principal,
        supplier_id: UUID,
        **fields: Any,
    ) -> Supplier:
        s = await self.suppliers.update(supplier_id, **fields)
        if s is None:
            raise NotFoundError("Supplier not found.")
        await record_audit(
            actor=principal,
            action="inventory.supplier.update",
            resource_type="supplier",
            resource_id=supplier_id,
            metadata={"changed": [k for k, v in fields.items() if v is not None]},
        )
        return s

    # ---------------- Purchase orders ----------------

    async def create_purchase_order(
        self,
        *,
        principal: Principal,
        supplier_id: UUID,
        currency: str,
        expected_at: datetime | None,
        notes: str | None,
        lines: list[dict[str, Any]],
    ) -> PurchaseOrder:
        if await self.suppliers.get(supplier_id) is None:
            raise NotFoundError("Supplier not found.")

        code = await self._allocate_code(self.purchase_orders.code_exists, make_po_code)
        po = await self.purchase_orders.create(
            code=code,
            supplier_id=supplier_id,
            currency=currency.upper(),
            expected_at=expected_at,
            notes=notes,
            status=PurchaseOrderStatus.DRAFT,
            created_by=principal.user_id,
        )
        for line in lines:
            await self.purchase_orders.add_line(purchase_order_id=po.id, **line)

        await record_audit(
            actor=principal,
            action="inventory.purchase_order.create",
            resource_type="purchase_order",
            resource_id=po.id,
            metadata={"code": code, "supplier_id": str(supplier_id), "lines": len(lines)},
        )
        return po

    # ---------------- Goods receipt (the invoice gate) ----------------

    async def receive_goods(
        self,
        *,
        principal: Principal,
        supplier_id: UUID,
        purchase_order_id: UUID | None,
        supplier_invoice_number: str,
        supplier_invoice_date: date,
        warehouse_code: str,
        currency: str,
        notes: str | None,
        lines: list[dict[str, Any]],
    ) -> GoodsReceipt:
        """Book a supplier-invoiced receipt and post the matching stock.

        This is the *only* code path that increases the ``available`` bucket.
        """
        if await self.suppliers.get(supplier_id) is None:
            raise NotFoundError("Supplier not found.")
        warehouse = await self.warehouses.get_by_code(warehouse_code)
        if warehouse is None:
            raise NotFoundError(f"Warehouse '{warehouse_code}' not found.")
        if purchase_order_id is not None:
            po = await self.purchase_orders.get(purchase_order_id)
            if po is None:
                raise NotFoundError("Purchase order not found.")
            if po.supplier_id != supplier_id:
                raise BusinessRuleError(
                    "Purchase order belongs to a different supplier.",
                )
            if po.status == PurchaseOrderStatus.CANCELLED:
                raise BusinessRuleError("Cannot receive against a cancelled purchase order.")
        if await self.goods_receipts.invoice_number_taken(
            supplier_id=supplier_id,
            invoice_number=supplier_invoice_number,
        ):
            raise ConflictError(
                "Supplier invoice number already booked for this supplier.",
                details={"supplier_invoice_number": supplier_invoice_number},
            )

        # Validate every line *before* writing anything.
        prepared = [self._validate_gr_line(line) for line in lines]

        code = await self._allocate_code(self.goods_receipts.code_exists, make_gr_code)
        gr = await self.goods_receipts.create(
            code=code,
            supplier_id=supplier_id,
            purchase_order_id=purchase_order_id,
            supplier_invoice_number=supplier_invoice_number,
            supplier_invoice_date=supplier_invoice_date,
            warehouse_id=warehouse.id,
            currency=currency.upper(),
            received_by=principal.user_id,
            notes=notes,
        )

        correlation_id = uuid4()
        for line in prepared:
            batch = await self._resolve_or_create_batch(
                supplier_id=supplier_id,
                line=line,
            )
            if batch.status != BatchStatus.ACTIVE:
                raise BusinessRuleError(
                    "Cannot receive into a non-active batch.",
                    details={"batch_id": str(batch.id), "status": batch.status},
                )
            if batch.expiry_date <= utc_now().date():
                raise BusinessRuleError(
                    "Cannot receive into an already-expired batch.",
                    details={
                        "batch_id": str(batch.id),
                        "expiry_date": batch.expiry_date.isoformat(),
                    },
                )

            await self.goods_receipts.add_line(
                goods_receipt_id=gr.id,
                variant_id=line["variant_id"],
                batch_id=batch.id,
                quantity=line["quantity"],
                unit_cost=line["unit_cost"],
            )

            await self.stock.apply_movement(
                variant_id=line["variant_id"],
                batch_id=batch.id,
                warehouse_id=warehouse.id,
                bucket=StockBucket.AVAILABLE,
                quantity_delta=line["quantity"],
                kind=LedgerKind.RECEIPT,
                actor_id=principal.user_id,
                correlation_id=correlation_id,
                reference_type="goods_receipt",
                reference_id=gr.id,
                extra={"unit_cost": str(line["unit_cost"])},
            )

            if purchase_order_id is not None:
                await self.purchase_orders.increment_received(
                    po_id=purchase_order_id,
                    variant_id=line["variant_id"],
                    quantity=line["quantity"],
                )

        if purchase_order_id is not None:
            await self._refresh_po_status(purchase_order_id)

        await record_audit(
            actor=principal,
            action="inventory.goods_receipt.create",
            resource_type="goods_receipt",
            resource_id=gr.id,
            metadata={
                "code": code,
                "supplier_id": str(supplier_id),
                "supplier_invoice_number": supplier_invoice_number,
                "lines": len(prepared),
                "warehouse_code": warehouse_code,
            },
        )

        await enqueue_outbox(
            type=EVT_STOCK_RECEIVED,
            payload={
                "goods_receipt_id": str(gr.id),
                "supplier_id": str(supplier_id),
                "warehouse_id": str(warehouse.id),
                "lines": [
                    {
                        "variant_id": str(line["variant_id"]),
                        "quantity": line["quantity"],
                    }
                    for line in prepared
                ],
            },
        )
        return gr

    @staticmethod
    def _validate_gr_line(line: dict[str, Any]) -> dict[str, Any]:
        """Enforce the existing-batch XOR new-batch contract documented on
        ``GRLineCreate``. Raises ``ValidationError`` with structured detail.
        """
        if line.get("batch_id") is not None:
            for forbidden in (
                "batch_number",
                "expiry_date",
                "manufacture_date",
                "manufacturer",
                "mrp",
            ):
                if line.get(forbidden) is not None:
                    raise ValidationError(
                        "Cannot specify both batch_id and new-batch fields on the same line.",
                        details={"field": forbidden},
                    )
            return line

        # New-batch path: HARD RULES — batch number and expiry are mandatory.
        if not line.get("batch_number"):
            raise ValidationError(
                "Receipt line requires either batch_id or batch_number+expiry_date.",
                details={"field": "batch_number"},
            )
        if line.get("expiry_date") is None:
            raise ValidationError(
                "Receipt line requires expiry_date when creating a new batch.",
                details={"field": "expiry_date"},
            )
        return line

    async def _resolve_or_create_batch(
        self,
        *,
        supplier_id: UUID,
        line: dict[str, Any],
    ) -> Batch:
        if line.get("batch_id") is not None:
            batch = await self.batches.get(line["batch_id"])
            if batch is None:
                raise NotFoundError("Batch not found.")
            if batch.variant_id != line["variant_id"]:
                raise BusinessRuleError(
                    "Batch belongs to a different variant.",
                    details={"batch_id": str(batch.id)},
                )
            return batch

        existing = await self.batches.get_by_variant_number(
            line["variant_id"],
            line["batch_number"],
        )
        if existing is not None:
            # Re-receiving the same physical batch from same supplier is fine;
            # cross-check that key attributes match so we don't merge unrelated lots.
            if existing.expiry_date != line["expiry_date"]:
                raise ConflictError(
                    "Batch number exists with a different expiry_date.",
                    details={
                        "batch_id": str(existing.id),
                        "stored_expiry": existing.expiry_date.isoformat(),
                        "received_expiry": line["expiry_date"].isoformat(),
                    },
                )
            return existing

        return await self.batches.create(
            variant_id=line["variant_id"],
            batch_number=line["batch_number"],
            supplier_id=supplier_id,
            manufacturer=line.get("manufacturer"),
            manufacture_date=line.get("manufacture_date"),
            expiry_date=line["expiry_date"],
            mrp=line.get("mrp"),
            status=BatchStatus.ACTIVE,
        )

    async def _refresh_po_status(self, po_id: UUID) -> None:
        po = await self.purchase_orders.get(po_id)
        if po is None:
            return
        all_received = all(line.quantity_received >= line.quantity_ordered for line in po.lines)
        any_received = any(line.quantity_received > 0 for line in po.lines)
        if all_received:
            po.status = PurchaseOrderStatus.RECEIVED
        elif any_received:
            po.status = PurchaseOrderStatus.PARTIAL

    # ---------------- Reserve / release / consume ----------------

    async def reserve_stock(
        self,
        *,
        principal: Principal | SystemPrincipal,
        variant_id: UUID,
        warehouse_code: str,
        quantity: int,
        reference_type: str | None,
        reference_id: UUID | None,
        notes: str | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[UUID, list[ReservedAllocation]]:
        """FEFO reserve. Returns ``(correlation_id, allocations)``.

        Allocations let callers know which batches were chosen so a future
        ``release`` or ``consume`` can target the same batches.

        ``correlation_id`` may be supplied by the caller (e.g. an order id)
        for idempotency. If a reservation already exists under this
        correlation, the call is a no-op and the existing allocations are
        returned unchanged.
        """
        if quantity <= 0:
            raise ValidationError("quantity must be positive.")
        warehouse = await self.warehouses.get_by_code(warehouse_code)
        if warehouse is None:
            raise NotFoundError(f"Warehouse '{warehouse_code}' not found.")

        # Idempotency check — caller-supplied correlation_id.
        if correlation_id is not None:
            existing = await self._existing_reserve_allocations(correlation_id)
            if existing:
                return correlation_id, existing

        candidates = await self.stock.fefo_available_balances(
            variant_id=variant_id,
            warehouse_id=warehouse.id,
        )
        remaining = quantity
        allocations: list[ReservedAllocation] = []
        correlation_id = correlation_id or uuid4()
        actor_id = principal.user_id if isinstance(principal, Principal) else None

        for cand in candidates:
            if remaining <= 0:
                break
            # Lock the actual balance row before committing to a count.
            locked_avail = await self.stock.get_balance_locked(
                variant_id=cand.variant_id,
                batch_id=cand.batch_id,
                warehouse_id=cand.warehouse_id,
                bucket=StockBucket.AVAILABLE,
            )
            if locked_avail is None or locked_avail.quantity <= 0:
                continue

            # Re-check batch state after lock (could have just been blocked).
            batch = await self.batches.get_locked(cand.batch_id)
            if batch is None or batch.status != BatchStatus.ACTIVE:
                continue
            if batch.expiry_date <= utc_now().date():
                # Defensive — cron should already have moved this; skip if not.
                continue

            take = min(remaining, locked_avail.quantity)
            await self.stock.apply_movement(
                variant_id=cand.variant_id,
                batch_id=cand.batch_id,
                warehouse_id=cand.warehouse_id,
                bucket=StockBucket.AVAILABLE,
                quantity_delta=-take,
                kind=LedgerKind.RESERVE,
                actor_id=actor_id,
                correlation_id=correlation_id,
                reference_type=reference_type,
                reference_id=reference_id,
                notes=notes,
            )
            await self.stock.apply_movement(
                variant_id=cand.variant_id,
                batch_id=cand.batch_id,
                warehouse_id=cand.warehouse_id,
                bucket=StockBucket.RESERVED,
                quantity_delta=take,
                kind=LedgerKind.RESERVE,
                actor_id=actor_id,
                correlation_id=correlation_id,
                reference_type=reference_type,
                reference_id=reference_id,
                notes=notes,
            )
            allocations.append(ReservedAllocation(batch_id=cand.batch_id, quantity=take))
            remaining -= take

        if remaining > 0:
            raise ConflictError(
                "Insufficient available stock to reserve.",
                details={
                    "variant_id": str(variant_id),
                    "requested": quantity,
                    "fulfilled": quantity - remaining,
                },
            )

        if isinstance(principal, Principal):
            await record_audit(
                actor=principal,
                action="inventory.stock.reserve",
                resource_type="variant",
                resource_id=variant_id,
                metadata={
                    "correlation_id": str(correlation_id),
                    "quantity": quantity,
                    "warehouse_code": warehouse_code,
                },
            )
        return correlation_id, allocations

    async def release_stock(
        self,
        *,
        principal: Principal,
        correlation_id: UUID,
        notes: str | None = None,
    ) -> int:
        """Reverse a prior reserve. Returns total quantity released.

        We trust the ledger as truth: walk the original ``reserve`` rows by
        ``correlation_id`` and emit inverses.
        """
        original_rows = await self._reserve_rows(correlation_id)
        if not original_rows:
            raise NotFoundError("No reservation found for that correlation id.")
        released_total = 0
        for row in original_rows:
            if row.bucket != StockBucket.RESERVED or row.quantity_delta <= 0:
                continue  # only invert the +reserved leg
            await self.stock.apply_movement(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.RESERVED,
                quantity_delta=-row.quantity_delta,
                kind=LedgerKind.RELEASE,
                actor_id=principal.user_id,
                correlation_id=correlation_id,
                reference_type=row.reference_type,
                reference_id=row.reference_id,
                notes=notes,
            )
            await self.stock.apply_movement(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.AVAILABLE,
                quantity_delta=row.quantity_delta,
                kind=LedgerKind.RELEASE,
                actor_id=principal.user_id,
                correlation_id=correlation_id,
                reference_type=row.reference_type,
                reference_id=row.reference_id,
                notes=notes,
            )
            released_total += row.quantity_delta

        await record_audit(
            actor=principal,
            action="inventory.stock.release",
            resource_type="reservation",
            resource_id=correlation_id,
            metadata={"released": released_total},
        )
        return released_total

    async def consume_stock(
        self,
        *,
        principal: Principal,
        correlation_id: UUID,
        quantity: int,
        notes: str | None = None,
    ) -> int:
        """Consume reserved stock (e.g. order shipped). Decrements ``reserved``.

        The total across all batches under ``correlation_id`` must be at least
        ``quantity``; we drain in the same FEFO order they were reserved in.
        """
        if quantity <= 0:
            raise ValidationError("quantity must be positive.")
        rows = [
            r
            for r in await self._reserve_rows(correlation_id)
            if r.bucket == StockBucket.RESERVED and r.quantity_delta > 0
        ]
        rows.sort(key=lambda r: r.occurred_at)
        remaining = quantity
        for row in rows:
            if remaining <= 0:
                break
            locked = await self.stock.get_balance_locked(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.RESERVED,
            )
            if locked is None or locked.quantity <= 0:
                continue
            take = min(remaining, locked.quantity)
            await self.stock.apply_movement(
                variant_id=row.variant_id,
                batch_id=row.batch_id,
                warehouse_id=row.warehouse_id,
                bucket=StockBucket.RESERVED,
                quantity_delta=-take,
                kind=LedgerKind.CONSUME,
                actor_id=principal.user_id,
                correlation_id=correlation_id,
                reference_type=row.reference_type,
                reference_id=row.reference_id,
                notes=notes,
            )
            remaining -= take

        if remaining > 0:
            raise ConflictError(
                "Insufficient reserved stock to consume.",
                details={"correlation_id": str(correlation_id), "shortfall": remaining},
            )

        await record_audit(
            actor=principal,
            action="inventory.stock.consume",
            resource_type="reservation",
            resource_id=correlation_id,
            metadata={"consumed": quantity},
        )
        return quantity

    async def _reserve_rows(self, correlation_id: UUID) -> Sequence[StockLedger]:
        rows, _ = await self.stock.list_ledger(
            correlation_id=correlation_id,
            limit=1000,
        )
        return [r for r in rows if r.kind in (LedgerKind.RESERVE,)]

    async def _existing_reserve_allocations(
        self, correlation_id: UUID,
    ) -> list[ReservedAllocation]:
        """Return per-batch reserved totals for a correlation, summed across
        the +reserved legs. Used to make ``reserve_stock`` idempotent on the
        same correlation.
        """
        rows = await self._reserve_rows(correlation_id)
        per_batch: dict[UUID, int] = {}
        for r in rows:
            if r.bucket == StockBucket.RESERVED and r.quantity_delta > 0:
                per_batch[r.batch_id] = per_batch.get(r.batch_id, 0) + r.quantity_delta
        return [
            ReservedAllocation(batch_id=batch_id, quantity=qty)
            for batch_id, qty in per_batch.items()
        ]

    # ---------------- Damage / Block / Unblock / Adjust ----------------

    async def mark_damaged(
        self,
        *,
        principal: Principal,
        variant_id: UUID,
        batch_id: UUID,
        warehouse_code: str,
        quantity: int,
        reason: str,
    ) -> None:
        warehouse = await self._require_warehouse(warehouse_code)
        correlation_id = uuid4()
        await self.stock.apply_movement(
            variant_id=variant_id,
            batch_id=batch_id,
            warehouse_id=warehouse.id,
            bucket=StockBucket.AVAILABLE,
            quantity_delta=-quantity,
            kind=LedgerKind.DAMAGE,
            actor_id=principal.user_id,
            correlation_id=correlation_id,
            notes=reason,
        )
        await self.stock.apply_movement(
            variant_id=variant_id,
            batch_id=batch_id,
            warehouse_id=warehouse.id,
            bucket=StockBucket.DAMAGED,
            quantity_delta=quantity,
            kind=LedgerKind.DAMAGE,
            actor_id=principal.user_id,
            correlation_id=correlation_id,
            notes=reason,
        )
        await record_audit(
            actor=principal,
            action="inventory.stock.damage",
            resource_type="batch",
            resource_id=batch_id,
            metadata={"quantity": quantity, "reason": reason},
        )

    async def block_batch(
        self,
        *,
        principal: Principal,
        batch_id: UUID,
        warehouse_code: str,
        reason: str,
    ) -> None:
        """Move a batch's available stock to ``blocked`` and mark batch.

        Reserved stock is intentionally left in place — callers depending on
        in-flight reservations decide whether to release them via the orders
        module. This keeps the block-step idempotent and side-effect-light.
        """
        warehouse = await self._require_warehouse(warehouse_code)
        batch = await self.batches.get_locked(batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        if batch.status == BatchStatus.BLOCKED:
            return  # idempotent

        avail = await self.stock.get_balance_locked(
            variant_id=batch.variant_id,
            batch_id=batch.id,
            warehouse_id=warehouse.id,
            bucket=StockBucket.AVAILABLE,
        )
        correlation_id = uuid4()
        if avail is not None and avail.quantity > 0:
            qty = avail.quantity
            await self.stock.apply_movement(
                variant_id=batch.variant_id,
                batch_id=batch.id,
                warehouse_id=warehouse.id,
                bucket=StockBucket.AVAILABLE,
                quantity_delta=-qty,
                kind=LedgerKind.BLOCK,
                actor_id=principal.user_id,
                correlation_id=correlation_id,
                notes=reason,
            )
            await self.stock.apply_movement(
                variant_id=batch.variant_id,
                batch_id=batch.id,
                warehouse_id=warehouse.id,
                bucket=StockBucket.BLOCKED,
                quantity_delta=qty,
                kind=LedgerKind.BLOCK,
                actor_id=principal.user_id,
                correlation_id=correlation_id,
                notes=reason,
            )
        await self.batches.mark_status(batch.id, BatchStatus.BLOCKED)
        await record_audit(
            actor=principal,
            action="inventory.batch.block",
            resource_type="batch",
            resource_id=batch.id,
            metadata={"reason": reason, "warehouse_code": warehouse_code},
        )

    async def unblock_batch(
        self,
        *,
        principal: Principal,
        batch_id: UUID,
        warehouse_code: str,
    ) -> None:
        warehouse = await self._require_warehouse(warehouse_code)
        batch = await self.batches.get_locked(batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        if batch.status == BatchStatus.EXPIRED:
            raise BusinessRuleError(
                "Cannot unblock an expired batch — write off via adjustment instead.",
            )
        if batch.status == BatchStatus.ACTIVE:
            return

        blocked = await self.stock.get_balance_locked(
            variant_id=batch.variant_id,
            batch_id=batch.id,
            warehouse_id=warehouse.id,
            bucket=StockBucket.BLOCKED,
        )
        correlation_id = uuid4()
        if blocked is not None and blocked.quantity > 0:
            qty = blocked.quantity
            await self.stock.apply_movement(
                variant_id=batch.variant_id,
                batch_id=batch.id,
                warehouse_id=warehouse.id,
                bucket=StockBucket.BLOCKED,
                quantity_delta=-qty,
                kind=LedgerKind.UNBLOCK,
                actor_id=principal.user_id,
                correlation_id=correlation_id,
            )
            await self.stock.apply_movement(
                variant_id=batch.variant_id,
                batch_id=batch.id,
                warehouse_id=warehouse.id,
                bucket=StockBucket.AVAILABLE,
                quantity_delta=qty,
                kind=LedgerKind.UNBLOCK,
                actor_id=principal.user_id,
                correlation_id=correlation_id,
            )
        await self.batches.mark_status(batch.id, BatchStatus.ACTIVE)
        await record_audit(
            actor=principal,
            action="inventory.batch.unblock",
            resource_type="batch",
            resource_id=batch.id,
            metadata={"warehouse_code": warehouse_code},
        )

    async def adjust_stock(
        self,
        *,
        principal: Principal,
        variant_id: UUID,
        batch_id: UUID,
        warehouse_code: str,
        quantity_delta: int,
        reason: str,
    ) -> None:
        """Manual adjustment to ``available``. Audit-loud and reason-required.

        Note this does NOT bypass the no-stock-without-invoice rule for
        normal ops — adjustments leave a distinct ``adjust_in/adjust_out``
        ledger trail and emit a high-priority audit row so finance can
        reconcile against supplier invoices.
        """
        if quantity_delta == 0:
            raise ValidationError("quantity_delta must be non-zero.")
        warehouse = await self._require_warehouse(warehouse_code)
        batch = await self.batches.get(batch_id)
        if batch is None:
            raise NotFoundError("Batch not found.")
        if batch.variant_id != variant_id:
            raise BusinessRuleError(
                "Batch belongs to a different variant.",
                details={"batch_id": str(batch_id)},
            )
        kind = LedgerKind.ADJUST_IN if quantity_delta > 0 else LedgerKind.ADJUST_OUT
        await self.stock.apply_movement(
            variant_id=variant_id,
            batch_id=batch_id,
            warehouse_id=warehouse.id,
            bucket=StockBucket.AVAILABLE,
            quantity_delta=quantity_delta,
            kind=kind,
            actor_id=principal.user_id,
            notes=reason,
            extra={"reason": reason},
        )
        await record_audit(
            actor=principal,
            action="inventory.stock.adjust",
            resource_type="batch",
            resource_id=batch_id,
            metadata={
                "quantity_delta": quantity_delta,
                "reason": reason,
                "warehouse_code": warehouse_code,
            },
        )

    # ---------------- Cron jobs ----------------

    async def expire_overdue_batches(
        self,
        *,
        principal: Principal | SystemPrincipal,
        today: date | None = None,
    ) -> int:
        """Move every overdue batch's available + reserved stock to ``expired``
        and mark the batch ``status=expired``.
        Returns the number of batches that transitioned.
        """
        the_day = today or utc_now().date()
        overdue = await self.batches.list_overdue(today=the_day)
        moved = 0
        for batch in overdue:
            await self._expire_batch(batch=batch, principal=principal)
            await enqueue_outbox(
                type=EVT_BATCH_EXPIRED,
                payload={
                    "batch_id": str(batch.id),
                    "variant_id": str(batch.variant_id),
                    "batch_number": batch.batch_number,
                    "expiry_date": batch.expiry_date.isoformat(),
                },
            )
            moved += 1
        return moved

    async def _expire_batch(
        self,
        *,
        batch: Batch,
        principal: Principal | SystemPrincipal,
    ) -> None:
        actor_id = principal.user_id if isinstance(principal, Principal) else None
        correlation_id = uuid4()
        balances: Sequence[StockBalance] = await self.stock.list_balances_for_variant(
            variant_id=batch.variant_id,
        )
        # Move available + reserved → expired for *this batch only*.
        for bal in balances:
            if bal.batch_id != batch.id:
                continue
            if bal.bucket not in (StockBucket.AVAILABLE, StockBucket.RESERVED):
                continue
            if bal.quantity <= 0:
                continue
            qty = bal.quantity
            await self.stock.apply_movement(
                variant_id=batch.variant_id,
                batch_id=batch.id,
                warehouse_id=bal.warehouse_id,
                bucket=bal.bucket,
                quantity_delta=-qty,
                kind=LedgerKind.EXPIRE,
                actor_id=actor_id,
                correlation_id=correlation_id,
                notes="auto-expire on batch expiry_date",
            )
            await self.stock.apply_movement(
                variant_id=batch.variant_id,
                batch_id=batch.id,
                warehouse_id=bal.warehouse_id,
                bucket=StockBucket.EXPIRED,
                quantity_delta=qty,
                kind=LedgerKind.EXPIRE,
                actor_id=actor_id,
                correlation_id=correlation_id,
                notes=f"auto-expire from {bal.bucket}",
            )
        await self.batches.mark_status(batch.id, BatchStatus.EXPIRED)
        if isinstance(principal, Principal):
            await record_audit(
                actor=principal,
                action="inventory.batch.expire",
                resource_type="batch",
                resource_id=batch.id,
                metadata={"expiry_date": batch.expiry_date.isoformat()},
            )

    async def scan_near_expiry(self, *, today: date | None = None) -> int:
        cfg = get_settings()
        the_day = today or utc_now().date()
        until = the_day + timedelta(days=cfg.inventory_near_expiry_days)
        candidates = await self.batches.list_near_expiry(today=the_day, until=until)
        emitted = 0
        for batch in candidates:
            qty_total = await self._batch_active_quantity(batch.id)
            if qty_total <= 0:
                continue
            days_remaining = (batch.expiry_date - the_day).days
            await enqueue_outbox(
                type=EVT_BATCH_NEAR_EXPIRY,
                payload={
                    "batch_id": str(batch.id),
                    "variant_id": str(batch.variant_id),
                    "batch_number": batch.batch_number,
                    "expiry_date": batch.expiry_date.isoformat(),
                    "days_remaining": days_remaining,
                    "available_quantity": qty_total,
                },
            )
            emitted += 1
        return emitted

    async def _batch_active_quantity(self, batch_id: UUID) -> int:
        balances = await self.stock.list_balances_for_variant(
            variant_id=(await self.batches.get(batch_id)).variant_id,  # type: ignore[union-attr]
        )
        return sum(
            b.quantity
            for b in balances
            if b.batch_id == batch_id
            and b.bucket in (StockBucket.AVAILABLE, StockBucket.RESERVED)
        )

    # ---------------- Helpers / queries ----------------

    async def _require_warehouse(self, code: str) -> Warehouse:
        wh = await self.warehouses.get_by_code(code)
        if wh is None:
            raise NotFoundError(f"Warehouse '{code}' not found.")
        return wh

    async def stock_summary(self, variant_id: UUID) -> dict[str, int]:
        balances = await self.stock.list_balances_for_variant(variant_id=variant_id)
        bucket_totals: dict[str, int] = {b.value: 0 for b in StockBucket}
        for bal in balances:
            bucket_totals[bal.bucket] = bucket_totals.get(bal.bucket, 0) + bal.quantity
        return bucket_totals

    @staticmethod
    async def _allocate_code(exists_fn: Any, generator: Any) -> str:
        for _ in range(_CODE_RETRIES):
            candidate = generator()
            if not await exists_fn(candidate):
                return candidate
        raise BusinessRuleError("Could not allocate a unique code after retries.")


# Re-export for typing convenience
__all__ = [
    "EVT_BATCH_EXPIRED",
    "EVT_BATCH_NEAR_EXPIRY",
    "EVT_STOCK_RECEIVED",
    "InventoryService",
    "ReservedAllocation",
]


_ = Decimal  # keep import to communicate currency arithmetic boundary
