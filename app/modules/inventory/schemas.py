from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.core.validation import StrictModel


# ---------------- Suppliers ----------------


class SupplierCreate(StrictModel):
    code: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Z0-9_\-]+$")
    name: str = Field(..., min_length=1, max_length=160)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=1024)
    tax_id: str | None = Field(default=None, max_length=64)
    linked_user_id: UUID | None = None
    is_active: bool = True


class SupplierUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=1024)
    tax_id: str | None = Field(default=None, max_length=64)
    linked_user_id: UUID | None = None
    is_active: bool | None = None


class SupplierResponse(StrictModel):
    id: UUID
    code: str
    name: str
    contact_email: str | None
    contact_phone: str | None
    address: str | None
    tax_id: str | None
    linked_user_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------- Warehouses ----------------


class WarehouseResponse(StrictModel):
    id: UUID
    code: str
    name: str
    is_active: bool


# ---------------- Purchase orders ----------------


class POLineCreate(StrictModel):
    variant_id: UUID
    quantity_ordered: int = Field(..., ge=1, le=10_000_000)
    unit_cost: Decimal = Field(..., max_digits=14, decimal_places=2, ge=0)


class PurchaseOrderCreate(StrictModel):
    supplier_id: UUID
    currency: str = Field(..., min_length=3, max_length=3)
    expected_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2048)
    lines: list[POLineCreate] = Field(..., min_length=1, max_length=500)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class POLineResponse(StrictModel):
    id: UUID
    variant_id: UUID
    quantity_ordered: int
    quantity_received: int
    unit_cost: Decimal


class PurchaseOrderResponse(StrictModel):
    id: UUID
    code: str
    supplier_id: UUID
    status: str
    currency: str
    expected_at: datetime | None
    notes: str | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    lines: list[POLineResponse]


# ---------------- Goods receipts ----------------


class GRLineCreate(StrictModel):
    """A single line on a goods-receipt invoice.

    Either ``batch_id`` (for an existing batch) OR the four batch-creation
    fields (``batch_number`` + ``expiry_date`` + optional manufacturer/mfg
    date) must be present. The service layer enforces this XOR.
    """

    variant_id: UUID
    quantity: int = Field(..., ge=1, le=10_000_000)
    unit_cost: Decimal = Field(..., max_digits=14, decimal_places=2, ge=0)

    # Existing-batch path:
    batch_id: UUID | None = None

    # New-batch path (HARD RULE: expiry mandatory when creating):
    batch_number: str | None = Field(default=None, min_length=1, max_length=64)
    expiry_date: date | None = None
    manufacture_date: date | None = None
    manufacturer: str | None = Field(default=None, max_length=160)
    mrp: Decimal | None = Field(default=None, max_digits=14, decimal_places=2, ge=0)


class GoodsReceiptCreate(StrictModel):
    supplier_id: UUID
    purchase_order_id: UUID | None = None
    supplier_invoice_number: str = Field(..., min_length=1, max_length=64)
    supplier_invoice_date: date
    warehouse_code: str = Field(..., min_length=1, max_length=32)
    currency: str = Field(..., min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2048)
    lines: list[GRLineCreate] = Field(..., min_length=1, max_length=1000)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class GRLineResponse(StrictModel):
    id: UUID
    variant_id: UUID
    batch_id: UUID
    quantity: int
    unit_cost: Decimal


class GoodsReceiptResponse(StrictModel):
    id: UUID
    code: str
    supplier_id: UUID
    purchase_order_id: UUID | None
    supplier_invoice_number: str
    supplier_invoice_date: date
    warehouse_id: UUID
    received_at: datetime
    received_by: UUID | None
    currency: str
    notes: str | None
    lines: list[GRLineResponse]


# ---------------- Batches ----------------


class BatchResponse(StrictModel):
    id: UUID
    variant_id: UUID
    batch_number: str
    supplier_id: UUID | None
    manufacturer: str | None
    manufacture_date: date | None
    expiry_date: date
    mrp: Decimal | None
    status: str


# ---------------- Stock movements + queries ----------------


class StockReserveRequest(StrictModel):
    quantity: int = Field(..., ge=1, le=10_000_000)
    reference_type: str | None = Field(default=None, max_length=48)
    reference_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=512)


class StockReleaseRequest(StrictModel):
    """Releases the *entire* reservation identified by ``correlation_id``.

    Partial release is not supported here — the orders module can issue a
    fresh reservation for the remainder if needed.
    """

    correlation_id: UUID
    notes: str | None = Field(default=None, max_length=512)


class StockConsumeRequest(StrictModel):
    quantity: int = Field(..., ge=1, le=10_000_000)
    correlation_id: UUID
    notes: str | None = Field(default=None, max_length=512)


class StockBucketTransferRequest(StrictModel):
    """Used for damage/block/unblock/adjust operations. Specifies a batch
    explicitly because operations like 'damage' apply to a physical batch,
    not to abstract variant stock.
    """

    batch_id: UUID
    quantity: int = Field(..., ge=1, le=10_000_000)
    reason: str = Field(..., min_length=1, max_length=255)


class StockAdjustRequest(StrictModel):
    batch_id: UUID
    quantity_delta: int = Field(..., ge=-10_000_000, le=10_000_000)
    reason: str = Field(..., min_length=1, max_length=255)

    @field_validator("quantity_delta")
    @classmethod
    def _nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("quantity_delta must be non-zero")
        return v


class StockBalanceRow(StrictModel):
    variant_id: UUID
    batch_id: UUID
    warehouse_id: UUID
    bucket: str
    quantity: int


class ReservedAllocationOut(StrictModel):
    batch_id: UUID
    quantity: int


class StockReserveResponse(StrictModel):
    correlation_id: UUID
    allocations: list[ReservedAllocationOut]


class StockSummary(StrictModel):
    variant_id: UUID
    by_bucket: dict[str, int]
    total: int
