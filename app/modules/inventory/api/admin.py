from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response

from app.core.config import get_settings
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.inventory.repository import (
    BatchRepository,
    GoodsReceiptRepository,
    PurchaseOrderRepository,
    StockRepository,
    SupplierRepository,
    WarehouseRepository,
)
from app.modules.inventory.schemas import (
    BatchResponse,
    GoodsReceiptCreate,
    GoodsReceiptResponse,
    GRLineResponse,
    POLineResponse,
    PurchaseOrderCreate,
    PurchaseOrderResponse,
    ReservedAllocationOut,
    StockAdjustRequest,
    StockBalanceRow,
    StockBucketTransferRequest,
    StockConsumeRequest,
    StockReleaseRequest,
    StockReserveRequest,
    StockReserveResponse,
    StockSummary,
    SupplierCreate,
    SupplierResponse,
    SupplierUpdate,
    WarehouseResponse,
)
from app.modules.inventory.service import InventoryService

router = APIRouter(prefix="/admin/inventory", tags=["admin-inventory"])

_READ = "inventory.read"
_RECEIVE = "inventory.receive"
_ADJUST = "inventory.adjust"


def _supplier_to_response(s) -> SupplierResponse:
    return SupplierResponse.model_validate(s)


def _po_to_response(po) -> PurchaseOrderResponse:
    return PurchaseOrderResponse(
        id=po.id,
        code=po.code,
        supplier_id=po.supplier_id,
        status=po.status,
        currency=po.currency,
        expected_at=po.expected_at,
        notes=po.notes,
        created_by=po.created_by,
        created_at=po.created_at,
        updated_at=po.updated_at,
        lines=[
            POLineResponse(
                id=line.id,
                variant_id=line.variant_id,
                quantity_ordered=line.quantity_ordered,
                quantity_received=line.quantity_received,
                unit_cost=line.unit_cost,
            )
            for line in po.lines
        ],
    )


def _gr_to_response(gr) -> GoodsReceiptResponse:
    return GoodsReceiptResponse(
        id=gr.id,
        code=gr.code,
        supplier_id=gr.supplier_id,
        purchase_order_id=gr.purchase_order_id,
        supplier_invoice_number=gr.supplier_invoice_number,
        supplier_invoice_date=gr.supplier_invoice_date,
        warehouse_id=gr.warehouse_id,
        received_at=gr.received_at,
        received_by=gr.received_by,
        currency=gr.currency,
        notes=gr.notes,
        lines=[
            GRLineResponse(
                id=line.id,
                variant_id=line.variant_id,
                batch_id=line.batch_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
            )
            for line in gr.lines
        ],
    )


# ---------------- Suppliers ----------------


@router.post(
    "/suppliers",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RECEIVE))],
)
async def create_supplier(
    payload: SupplierCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SupplierResponse:
    fields = payload.model_dump()
    if fields.get("contact_email") is not None:
        fields["contact_email"] = str(fields["contact_email"])
    async with uow.transactional() as session:
        svc = InventoryService(session)
        s = await svc.create_supplier(principal=principal, **fields)
        return _supplier_to_response(s)


@router.get(
    "/suppliers",
    response_model=list[SupplierResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_suppliers(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    active_only: bool = Query(default=False),
) -> list[SupplierResponse]:
    async with uow.transactional() as session:
        repo = SupplierRepository(session)
        rows = await repo.list_all(active_only=active_only)
        return [_supplier_to_response(r) for r in rows]


@router.patch(
    "/suppliers/{supplier_id}",
    response_model=SupplierResponse,
    dependencies=[Depends(requires_permission(_RECEIVE))],
)
async def update_supplier(
    supplier_id: UUID,
    payload: SupplierUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SupplierResponse:
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("contact_email") is not None:
        fields["contact_email"] = str(fields["contact_email"])
    async with uow.transactional() as session:
        svc = InventoryService(session)
        s = await svc.update_supplier(principal=principal, supplier_id=supplier_id, **fields)
        return _supplier_to_response(s)


# ---------------- Warehouses ----------------


@router.get(
    "/warehouses",
    response_model=list[WarehouseResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_warehouses(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[WarehouseResponse]:
    async with uow.transactional() as session:
        repo = WarehouseRepository(session)
        return [WarehouseResponse.model_validate(w) for w in await repo.list_active()]


# ---------------- Purchase orders ----------------


@router.post(
    "/purchase-orders",
    response_model=PurchaseOrderResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RECEIVE))],
)
async def create_purchase_order(
    payload: PurchaseOrderCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PurchaseOrderResponse:
    async with uow.transactional() as session:
        svc = InventoryService(session)
        po = await svc.create_purchase_order(
            principal=principal,
            supplier_id=payload.supplier_id,
            currency=payload.currency,
            expected_at=payload.expected_at,
            notes=payload.notes,
            lines=[line.model_dump() for line in payload.lines],
        )
        return _po_to_response(po)


@router.get(
    "/purchase-orders",
    response_model=Page[PurchaseOrderResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_purchase_orders(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    supplier_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> Page[PurchaseOrderResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = PurchaseOrderRepository(session)
        rows, total = await repo.list_paginated(
            offset=params.offset,
            limit=params.limit,
            supplier_id=supplier_id,
            status=status_filter,
        )
    return Page.build(items=[_po_to_response(p) for p in rows], total=total, params=params)


@router.get(
    "/purchase-orders/{po_id}",
    response_model=PurchaseOrderResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_purchase_order(
    po_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PurchaseOrderResponse:
    async with uow.transactional() as session:
        po = await PurchaseOrderRepository(session).get(po_id)
        if po is None:
            raise NotFoundError("Purchase order not found.")
        return _po_to_response(po)


# ---------------- Goods receipts ----------------


@router.post(
    "/goods-receipts",
    response_model=GoodsReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a supplier-invoiced receipt of goods",
    description=(
        "The only code path that increases the ``available`` stock bucket. "
        "Refuses without a valid supplier invoice number; refuses duplicate "
        "(supplier, invoice_number) pairs."
    ),
    dependencies=[Depends(requires_permission(_RECEIVE))],
)
async def create_goods_receipt(
    payload: GoodsReceiptCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> GoodsReceiptResponse:
    async with uow.transactional() as session:
        svc = InventoryService(session)
        gr = await svc.receive_goods(
            principal=principal,
            supplier_id=payload.supplier_id,
            purchase_order_id=payload.purchase_order_id,
            supplier_invoice_number=payload.supplier_invoice_number,
            supplier_invoice_date=payload.supplier_invoice_date,
            warehouse_code=payload.warehouse_code,
            currency=payload.currency,
            notes=payload.notes,
            lines=[line.model_dump() for line in payload.lines],
        )
        return _gr_to_response(gr)


@router.get(
    "/goods-receipts",
    response_model=Page[GoodsReceiptResponse],
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_goods_receipts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
    supplier_id: UUID | None = Query(default=None),
) -> Page[GoodsReceiptResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = GoodsReceiptRepository(session)
        rows, total = await repo.list_paginated(
            offset=params.offset,
            limit=params.limit,
            supplier_id=supplier_id,
        )
    return Page.build(items=[_gr_to_response(g) for g in rows], total=total, params=params)


@router.get(
    "/goods-receipts/{gr_id}",
    response_model=GoodsReceiptResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_goods_receipt(
    gr_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> GoodsReceiptResponse:
    async with uow.transactional() as session:
        gr = await GoodsReceiptRepository(session).get(gr_id)
        if gr is None:
            raise NotFoundError("Goods receipt not found.")
        return _gr_to_response(gr)


# ---------------- Stock query ----------------


@router.get(
    "/stock/{variant_id}",
    response_model=StockSummary,
    summary="Per-bucket stock summary for a variant",
    dependencies=[Depends(requires_permission(_READ))],
)
async def stock_summary(
    variant_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> StockSummary:
    async with uow.transactional() as session:
        svc = InventoryService(session)
        by_bucket = await svc.stock_summary(variant_id)
        return StockSummary(
            variant_id=variant_id,
            by_bucket=by_bucket,
            total=sum(by_bucket.values()),
        )


@router.get(
    "/stock/{variant_id}/balances",
    response_model=list[StockBalanceRow],
    dependencies=[Depends(requires_permission(_READ))],
)
async def stock_balances(
    variant_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[StockBalanceRow]:
    async with uow.transactional() as session:
        repo = StockRepository(session)
        rows = await repo.list_balances_for_variant(variant_id=variant_id)
        return [
            StockBalanceRow(
                variant_id=r.variant_id,
                batch_id=r.batch_id,
                warehouse_id=r.warehouse_id,
                bucket=r.bucket,
                quantity=r.quantity,
            )
            for r in rows
        ]


# ---------------- Stock movements ----------------


@router.post(
    "/stock/{variant_id}/reserve",
    response_model=StockReserveResponse,
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def reserve_stock(
    variant_id: UUID,
    payload: StockReserveRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    warehouse_code: str = Query(default=""),
) -> StockReserveResponse:
    cfg = get_settings()
    code = warehouse_code or cfg.inventory_default_warehouse_code
    async with uow.transactional() as session:
        svc = InventoryService(session)
        correlation_id, allocs = await svc.reserve_stock(
            principal=principal,
            variant_id=variant_id,
            warehouse_code=code,
            quantity=payload.quantity,
            reference_type=payload.reference_type,
            reference_id=payload.reference_id,
            notes=payload.notes,
        )
        return StockReserveResponse(
            correlation_id=correlation_id,
            allocations=[
                ReservedAllocationOut(batch_id=a.batch_id, quantity=a.quantity)
                for a in allocs
            ],
        )


@router.post(
    "/stock/release",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def release_stock(
    payload: StockReleaseRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = InventoryService(session)
        await svc.release_stock(
            principal=principal,
            correlation_id=payload.correlation_id,
            notes=payload.notes,
        )


@router.post(
    "/stock/consume",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def consume_stock(
    payload: StockConsumeRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = InventoryService(session)
        await svc.consume_stock(
            principal=principal,
            correlation_id=payload.correlation_id,
            quantity=payload.quantity,
            notes=payload.notes,
        )


@router.post(
    "/stock/{variant_id}/damage",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def damage_stock(
    variant_id: UUID,
    payload: StockBucketTransferRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    warehouse_code: str = Query(default=""),
) -> None:
    cfg = get_settings()
    code = warehouse_code or cfg.inventory_default_warehouse_code
    async with uow.transactional() as session:
        svc = InventoryService(session)
        await svc.mark_damaged(
            principal=principal,
            variant_id=variant_id,
            batch_id=payload.batch_id,
            warehouse_code=code,
            quantity=payload.quantity,
            reason=payload.reason,
        )


@router.post(
    "/batches/{batch_id}/block",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def block_batch(
    batch_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    warehouse_code: str = Query(default=""),
    reason: str = Query(..., min_length=1, max_length=255),
) -> None:
    cfg = get_settings()
    code = warehouse_code or cfg.inventory_default_warehouse_code
    async with uow.transactional() as session:
        svc = InventoryService(session)
        await svc.block_batch(
            principal=principal,
            batch_id=batch_id,
            warehouse_code=code,
            reason=reason,
        )


@router.post(
    "/batches/{batch_id}/unblock",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def unblock_batch(
    batch_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    warehouse_code: str = Query(default=""),
) -> None:
    cfg = get_settings()
    code = warehouse_code or cfg.inventory_default_warehouse_code
    async with uow.transactional() as session:
        svc = InventoryService(session)
        await svc.unblock_batch(
            principal=principal,
            batch_id=batch_id,
            warehouse_code=code,
        )


@router.post(
    "/stock/{variant_id}/adjust",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def adjust_stock(
    variant_id: UUID,
    payload: StockAdjustRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    warehouse_code: str = Query(default=""),
) -> None:
    cfg = get_settings()
    code = warehouse_code or cfg.inventory_default_warehouse_code
    async with uow.transactional() as session:
        svc = InventoryService(session)
        await svc.adjust_stock(
            principal=principal,
            variant_id=variant_id,
            batch_id=payload.batch_id,
            warehouse_code=code,
            quantity_delta=payload.quantity_delta,
            reason=payload.reason,
        )


# ---------------- Batch lookup ----------------


@router.get(
    "/batches/{batch_id}",
    response_model=BatchResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_batch(
    batch_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> BatchResponse:
    async with uow.transactional() as session:
        b = await BatchRepository(session).get(batch_id)
        if b is None:
            raise NotFoundError("Batch not found.")
        return BatchResponse.model_validate(b)


# ---------------- Manual job triggers ----------------


@router.post(
    "/jobs/expire-overdue",
    status_code=status.HTTP_200_OK,
    summary="Manually run the expired-batch sweep",
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def trigger_expire_overdue(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, int]:
    async with uow.transactional() as session:
        svc = InventoryService(session)
        moved = await svc.expire_overdue_batches(principal=principal)
        return {"batches_expired": moved}


@router.post(
    "/jobs/near-expiry-scan",
    status_code=status.HTTP_200_OK,
    summary="Manually run the near-expiry alert scan",
    dependencies=[Depends(requires_permission(_ADJUST))],
)
async def trigger_near_expiry_scan(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, int]:
    async with uow.transactional() as session:
        svc = InventoryService(session)
        emitted = await svc.scan_near_expiry()
        return {"alerts_emitted": emitted}
