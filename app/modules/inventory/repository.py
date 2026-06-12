from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.modules.inventory.models import (
    Batch,
    BatchStatus,
    GoodsReceipt,
    GoodsReceiptLine,
    LedgerKind,
    PurchaseOrder,
    PurchaseOrderLine,
    StockBalance,
    StockBucket,
    StockLedger,
    Supplier,
    Warehouse,
)


class SupplierRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, supplier_id: UUID) -> Supplier | None:
        return await self.session.get(Supplier, supplier_id)

    async def get_by_code(self, code: str) -> Supplier | None:
        return (
            await self.session.execute(select(Supplier).where(Supplier.code == code))
        ).scalar_one_or_none()

    async def list_all(self, *, active_only: bool = False) -> Sequence[Supplier]:
        stmt = select(Supplier).order_by(Supplier.name)
        if active_only:
            stmt = stmt.where(Supplier.is_active.is_(True))
        return (await self.session.execute(stmt)).scalars().all()

    async def create(self, **fields: object) -> Supplier:
        s = Supplier(**fields)
        self.session.add(s)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Supplier code or name already exists.") from e
        return s

    async def update(self, supplier_id: UUID, **fields: object) -> Supplier | None:
        s = await self.session.get(Supplier, supplier_id)
        if s is None:
            return None
        for k, v in fields.items():
            if v is not None:
                setattr(s, k, v)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Supplier code or name already exists.") from e
        return s


class WarehouseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(self, code: str) -> Warehouse | None:
        return (
            await self.session.execute(select(Warehouse).where(Warehouse.code == code))
        ).scalar_one_or_none()

    async def list_active(self) -> Sequence[Warehouse]:
        stmt = (
            select(Warehouse).where(Warehouse.is_active.is_(True)).order_by(Warehouse.code)
        )
        return (await self.session.execute(stmt)).scalars().all()


class PurchaseOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, po_id: UUID) -> PurchaseOrder | None:
        return await self.session.get(PurchaseOrder, po_id)

    async def code_exists(self, code: str) -> bool:
        return (
            await self.session.execute(select(PurchaseOrder.id).where(PurchaseOrder.code == code))
        ).scalar_one_or_none() is not None

    async def create(self, **fields: object) -> PurchaseOrder:
        po = PurchaseOrder(**fields)
        self.session.add(po)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Purchase order code collision.") from e
        return po

    async def add_line(self, **fields: object) -> PurchaseOrderLine:
        line = PurchaseOrderLine(**fields)
        self.session.add(line)
        await self.session.flush()
        return line

    async def increment_received(
        self,
        *,
        po_id: UUID,
        variant_id: UUID,
        quantity: int,
    ) -> None:
        await self.session.execute(
            update(PurchaseOrderLine)
            .where(
                PurchaseOrderLine.purchase_order_id == po_id,
                PurchaseOrderLine.variant_id == variant_id,
            )
            .values(quantity_received=PurchaseOrderLine.quantity_received + quantity),
        )

    async def list_paginated(
        self,
        *,
        offset: int,
        limit: int,
        supplier_id: UUID | None = None,
        status: str | None = None,
    ) -> tuple[Sequence[PurchaseOrder], int]:
        conds: list[Any] = []
        if supplier_id is not None:
            conds.append(PurchaseOrder.supplier_id == supplier_id)
        if status is not None:
            conds.append(PurchaseOrder.status == status)
        count_stmt = select(func.count()).select_from(PurchaseOrder)
        list_stmt = select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (
            (await self.session.execute(list_stmt.offset(offset).limit(limit))).scalars().all()
        )
        return rows, int(total)


class BatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, batch_id: UUID) -> Batch | None:
        return await self.session.get(Batch, batch_id)

    async def get_locked(self, batch_id: UUID) -> Batch | None:
        stmt = select(Batch).where(Batch.id == batch_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_variant_number(self, variant_id: UUID, number: str) -> Batch | None:
        stmt = select(Batch).where(
            Batch.variant_id == variant_id, Batch.batch_number == number
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, **fields: object) -> Batch:
        b = Batch(**fields)
        self.session.add(b)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError("Batch number already exists for this variant.") from e
        return b

    async def list_overdue(self, *, today: date) -> Sequence[Batch]:
        stmt = (
            select(Batch)
            .where(Batch.expiry_date < today, Batch.status == BatchStatus.ACTIVE)
            .order_by(Batch.expiry_date)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_near_expiry(self, *, today: date, until: date) -> Sequence[Batch]:
        stmt = (
            select(Batch)
            .where(
                Batch.expiry_date >= today,
                Batch.expiry_date <= until,
                Batch.status == BatchStatus.ACTIVE,
            )
            .order_by(Batch.expiry_date)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def mark_status(self, batch_id: UUID, status: BatchStatus) -> None:
        await self.session.execute(
            update(Batch).where(Batch.id == batch_id).values(status=status),
        )


class GoodsReceiptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, gr_id: UUID) -> GoodsReceipt | None:
        return await self.session.get(GoodsReceipt, gr_id)

    async def code_exists(self, code: str) -> bool:
        return (
            await self.session.execute(select(GoodsReceipt.id).where(GoodsReceipt.code == code))
        ).scalar_one_or_none() is not None

    async def invoice_number_taken(
        self, *, supplier_id: UUID, invoice_number: str,
    ) -> bool:
        stmt = select(GoodsReceipt.id).where(
            GoodsReceipt.supplier_id == supplier_id,
            GoodsReceipt.supplier_invoice_number == invoice_number,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def create(self, **fields: object) -> GoodsReceipt:
        gr = GoodsReceipt(**fields)
        self.session.add(gr)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError(
                "Supplier invoice number already booked for this supplier.",
            ) from e
        return gr

    async def add_line(self, **fields: object) -> GoodsReceiptLine:
        line = GoodsReceiptLine(**fields)
        self.session.add(line)
        await self.session.flush()
        return line

    async def list_paginated(
        self,
        *,
        offset: int,
        limit: int,
        supplier_id: UUID | None = None,
    ) -> tuple[Sequence[GoodsReceipt], int]:
        conds: list[Any] = []
        if supplier_id is not None:
            conds.append(GoodsReceipt.supplier_id == supplier_id)
        count_stmt = select(func.count()).select_from(GoodsReceipt)
        list_stmt = select(GoodsReceipt).order_by(GoodsReceipt.received_at.desc())
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(*conds)
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (
            (await self.session.execute(list_stmt.offset(offset).limit(limit))).scalars().all()
        )
        return rows, int(total)


class StockRepository:
    """Combined ledger + balance repo. Every mutation writes both rows.

    Service layer should call ``apply_movement`` rather than touching the
    ledger or balance tables directly so the two stay in lockstep.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_balance_locked(
        self,
        *,
        variant_id: UUID,
        batch_id: UUID,
        warehouse_id: UUID,
        bucket: StockBucket,
    ) -> StockBalance | None:
        stmt = (
            select(StockBalance)
            .where(
                StockBalance.variant_id == variant_id,
                StockBalance.batch_id == batch_id,
                StockBalance.warehouse_id == warehouse_id,
                StockBalance.bucket == bucket,
            )
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_balances_for_variant(
        self,
        *,
        variant_id: UUID,
        bucket: StockBucket | None = None,
    ) -> Sequence[StockBalance]:
        conds: list[Any] = [StockBalance.variant_id == variant_id]
        if bucket is not None:
            conds.append(StockBalance.bucket == bucket)
        stmt = select(StockBalance).where(*conds)
        return (await self.session.execute(stmt)).scalars().all()

    async def fefo_available_balances(
        self, *, variant_id: UUID, warehouse_id: UUID,
    ) -> Sequence[StockBalance]:
        """Return AVAILABLE balances for a variant in expiry-soonest-first order,
        excluding expired/blocked batches. Used by the reservation algorithm.
        Rows are NOT locked here — caller locks each as it consumes.
        """
        stmt = (
            select(StockBalance)
            .join(Batch, Batch.id == StockBalance.batch_id)
            .where(
                StockBalance.variant_id == variant_id,
                StockBalance.warehouse_id == warehouse_id,
                StockBalance.bucket == StockBucket.AVAILABLE,
                StockBalance.quantity > 0,
                Batch.status == BatchStatus.ACTIVE,
            )
            .order_by(Batch.expiry_date.asc(), Batch.created_at.asc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def apply_movement(
        self,
        *,
        variant_id: UUID,
        batch_id: UUID,
        warehouse_id: UUID,
        bucket: StockBucket,
        quantity_delta: int,
        kind: LedgerKind,
        actor_id: UUID | None,
        correlation_id: UUID | None = None,
        reference_type: str | None = None,
        reference_id: UUID | None = None,
        notes: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> StockLedger:
        """Insert one ledger row and adjust the matching balance row.

        ``quantity_delta`` is signed: positive adds to the bucket, negative
        removes. The balance row's CHECK ``quantity >= 0`` will fail if a
        caller tries to over-decrement; that's a service-layer bug, not a
        condition we want to silently swallow.
        """
        if quantity_delta == 0:
            raise ValueError("quantity_delta must be non-zero")

        # Step 1 — ensure a balance row exists for this grain. We use
        # INSERT ... ON CONFLICT DO NOTHING so two concurrent transactions
        # creating the *same* (variant, batch, warehouse, bucket) row don't
        # race on the UNIQUE constraint. The losing tx sees DO NOTHING and
        # then waits on the SELECT ... FOR UPDATE below.
        await self.session.execute(
            pg_insert(StockBalance.__table__)
            .values(
                variant_id=variant_id,
                batch_id=batch_id,
                warehouse_id=warehouse_id,
                bucket=bucket.value,
                quantity=0,
            )
            .on_conflict_do_nothing(
                constraint="uq_stock_balances_grain",
            )
        )

        # Step 2 — lock the row. Now guaranteed to exist.
        bal = await self.get_balance_locked(
            variant_id=variant_id,
            batch_id=batch_id,
            warehouse_id=warehouse_id,
            bucket=bucket,
        )
        if bal is None:
            # Theoretically impossible after the upsert; guard anyway.
            raise ConflictError(
                "Balance row missing after upsert — concurrent schema change?",
                details={
                    "variant_id": str(variant_id),
                    "batch_id": str(batch_id),
                    "bucket": bucket.value,
                },
            )
        new_qty = bal.quantity + quantity_delta
        if new_qty < 0:
            raise ConflictError(
                "Insufficient stock in bucket.",
                details={
                    "variant_id": str(variant_id),
                    "batch_id": str(batch_id),
                    "bucket": bucket.value,
                    "have": bal.quantity,
                    "want_to_remove": -quantity_delta,
                },
            )
        bal.quantity = new_qty

        ledger = StockLedger(
            variant_id=variant_id,
            batch_id=batch_id,
            warehouse_id=warehouse_id,
            bucket=bucket,
            quantity_delta=quantity_delta,
            kind=kind,
            actor_id=actor_id,
            correlation_id=correlation_id,
            reference_type=reference_type,
            reference_id=reference_id,
            notes=notes,
            extra=extra or {},
        )
        self.session.add(ledger)
        await self.session.flush()
        return ledger

    async def list_ledger(
        self,
        *,
        variant_id: UUID | None = None,
        batch_id: UUID | None = None,
        correlation_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[Sequence[StockLedger], int]:
        conds: list[Any] = []
        if variant_id is not None:
            conds.append(StockLedger.variant_id == variant_id)
        if batch_id is not None:
            conds.append(StockLedger.batch_id == batch_id)
        if correlation_id is not None:
            conds.append(StockLedger.correlation_id == correlation_id)
        count_stmt = select(func.count()).select_from(StockLedger)
        list_stmt = select(StockLedger).order_by(StockLedger.occurred_at.desc())
        if conds:
            count_stmt = count_stmt.where(*conds)
            list_stmt = list_stmt.where(and_(*conds))
        total = (await self.session.execute(count_stmt)).scalar_one() or 0
        rows = (
            (await self.session.execute(list_stmt.offset(offset).limit(limit))).scalars().all()
        )
        return rows, int(total)
