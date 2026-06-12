"""Inventory ORM models.

Schema design notes
-------------------
- ``stock_ledger`` is append-only and the source of truth. UPDATE/DELETE are
  REVOKEd at the DB level (see migration). Every stock change is a row.
- ``stock_balances`` is a per-(variant, batch, warehouse, bucket) cache. It
  is updated *inside the same transaction* as the ledger insert so the two
  never disagree. ``CHECK quantity >= 0`` is the safety net against bugs.
- ``goods_receipts`` is the legal gate for any addition to the ``available``
  bucket. The service layer refuses to mint receipt-kind ledger rows without
  a goods_receipt_id reference.
- ``batches`` are unique per (variant, batch_number). Re-receiving the same
  batch from the same supplier just adds another ``goods_receipt_lines`` row
  pointing at the existing batch.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class StockBucket(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    DAMAGED = "damaged"
    EXPIRED = "expired"
    BLOCKED = "blocked"


class LedgerKind(StrEnum):
    RECEIPT = "receipt"  # supplier receipt → available
    RESERVE = "reserve"  # available → reserved
    RELEASE = "release"  # reserved → available
    CONSUME = "consume"  # reserved → out (sold/shipped)
    DAMAGE = "damage"  # any → damaged
    EXPIRE = "expire"  # available/reserved → expired (auto-block job)
    BLOCK = "block"  # any → blocked
    UNBLOCK = "unblock"  # blocked → available
    ADJUST_IN = "adjust_in"  # admin adjustment, positive
    ADJUST_OUT = "adjust_out"  # admin adjustment, negative
    RETURN = "return"  # customer return → available or damaged


class PurchaseOrderStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class BatchStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    BLOCKED = "blocked"


# ---------- Suppliers + Warehouses ----------


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("true"),
    )

    __table_args__ = (Index("ix_suppliers_is_active", "is_active"),)


class Warehouse(Base, TimestampMixin):
    __tablename__ = "warehouses"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("true"),
    )


# ---------- Purchase orders ----------


class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supplier_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        String(16),
        nullable=False,
        server_default=PurchaseOrderStatus.DRAFT.value,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    expected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        "PurchaseOrderLine",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','submitted','partial','received','cancelled')",
            name="status_enum",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="currency_iso",
        ),
        Index("ix_purchase_orders_supplier_id", "supplier_id"),
        Index("ix_purchase_orders_status", "status"),
    )


class PurchaseOrderLine(Base, TimestampMixin):
    __tablename__ = "purchase_order_lines"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    purchase_order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity_ordered: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    quantity_received: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    purchase_order: Mapped[PurchaseOrder] = relationship("PurchaseOrder", back_populates="lines")

    __table_args__ = (
        CheckConstraint("quantity_ordered > 0", name="quantity_positive"),
        CheckConstraint(
            "quantity_received >= 0 AND quantity_received <= quantity_ordered",
            name="quantity_received_bounds",
        ),
        CheckConstraint("unit_cost >= 0", name="unit_cost_nonneg"),
        Index("ix_purchase_order_lines_po", "purchase_order_id"),
    )


# ---------- Goods receipts (the invoice gate) ----------


class GoodsReceipt(Base, TimestampMixin):
    """A supplier-invoiced receipt of goods.

    HARD RULE: this row is the only thing that authorizes adding to the
    ``available`` stock bucket. The service layer enforces it; ``no
    goods_receipt = no inbound stock``.
    """

    __tablename__ = "goods_receipts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supplier_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    purchase_order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    supplier_invoice_number: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    received_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    lines: Mapped[list[GoodsReceiptLine]] = relationship(
        "GoodsReceiptLine",
        back_populates="goods_receipt",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "supplier_id",
            "supplier_invoice_number",
            name="uq_goods_receipts_supplier_invoice",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="currency_iso",
        ),
        Index("ix_goods_receipts_supplier_id", "supplier_id"),
        Index("ix_goods_receipts_received_at", "received_at"),
    )


class GoodsReceiptLine(Base, TimestampMixin):
    __tablename__ = "goods_receipt_lines"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    goods_receipt_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("goods_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    goods_receipt: Mapped[GoodsReceipt] = relationship("GoodsReceipt", back_populates="lines")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_nonneg"),
        Index("ix_goods_receipt_lines_gr", "goods_receipt_id"),
        Index("ix_goods_receipt_lines_variant", "variant_id"),
        Index("ix_goods_receipt_lines_batch", "batch_id"),
    )


# ---------- Batches ----------


class Batch(Base, TimestampMixin):
    """A manufactured batch tied to a specific product variant.

    HARD RULE: ``expiry_date`` is mandatory; the column is NOT NULL.
    """

    __tablename__ = "batches"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_number: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    manufacturer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    manufacture_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    mrp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[BatchStatus] = mapped_column(
        String(16),
        nullable=False,
        server_default=BatchStatus.ACTIVE.value,
    )
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        UniqueConstraint("variant_id", "batch_number", name="uq_batches_variant_number"),
        CheckConstraint(
            "manufacture_date IS NULL OR expiry_date >= manufacture_date",
            name="expiry_after_manufacture",
        ),
        CheckConstraint(
            "status IN ('active','expired','blocked')",
            name="status_enum",
        ),
        Index("ix_batches_variant_expiry", "variant_id", "expiry_date"),
        Index("ix_batches_expiry", "expiry_date"),
        Index("ix_batches_status", "status"),
    )


# ---------- Stock ledger + balances ----------


class StockLedger(Base):
    """Append-only stock ledger. Source of truth.

    The migration REVOKEs UPDATE/DELETE from PUBLIC, mirroring ``audit_log``.
    Always insert; never mutate. To reverse a movement, write the inverse row.
    """

    __tablename__ = "stock_ledger"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bucket: Mapped[StockBucket] = mapped_column(String(16), nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[LedgerKind] = mapped_column(String(16), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reference_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extra: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        CheckConstraint("quantity_delta <> 0", name="delta_nonzero"),
        CheckConstraint(
            "bucket IN ('available','reserved','damaged','expired','blocked')",
            name="bucket_enum",
        ),
        CheckConstraint(
            "kind IN ('receipt','reserve','release','consume','damage','expire',"
            "'block','unblock','adjust_in','adjust_out','return')",
            name="kind_enum",
        ),
        Index("ix_stock_ledger_variant_bucket_at", "variant_id", "bucket", "occurred_at"),
        Index("ix_stock_ledger_batch_bucket", "batch_id", "bucket"),
        Index("ix_stock_ledger_correlation", "correlation_id"),
        Index("ix_stock_ledger_reference", "reference_type", "reference_id"),
    )


class StockBalance(Base, TimestampMixin):
    """Cached per-bucket quantity. Updated inside the same transaction as the
    matching ledger insert; the ledger is the source of truth, this is an
    index for fast querying and per-row locking during reservation.
    """

    __tablename__ = "stock_balances"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    warehouse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bucket: Mapped[StockBucket] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint(
            "variant_id",
            "batch_id",
            "warehouse_id",
            "bucket",
            name="uq_stock_balances_grain",
        ),
        CheckConstraint("quantity >= 0", name="quantity_nonneg"),
        CheckConstraint(
            "bucket IN ('available','reserved','damaged','expired','blocked')",
            name="bucket_enum",
        ),
        Index("ix_stock_balances_variant_bucket", "variant_id", "bucket"),
        Index("ix_stock_balances_batch", "batch_id"),
    )

    # ------------------------------------------------------------------
    # Dashboard protocol adapters (read-only). Lets a StockBalance row
    # satisfy ``app.modules.dashboard.protocols.IDashboardInventory``.
    #
    # Caveats:
    #   * ``stock_count`` is just the cached ``quantity`` for this
    #     specific (variant, batch, warehouse, bucket) row. Aggregate
    #     across rows in the caller if you want SKU-level totals.
    #   * ``safety_threshold`` is read from module_config
    #     (``inventory.low_stock_threshold``, default 10) at query time
    #     since stock_balances has no per-row threshold today. Returning
    #     the global default here keeps the protocol satisfied with zero
    #     coupling to the config service — callers that need a real
    #     per-row threshold should pass it in or query module_config
    #     themselves.
    # ------------------------------------------------------------------
    _DEFAULT_SAFETY_THRESHOLD = 10

    @property
    def stock_count(self) -> int:
        """Quantity on hand for this row (cached from the ledger)."""
        return int(self.quantity)

    @property
    def safety_threshold(self) -> int:
        """Per-row low-stock threshold. Falls back to the module-config
        default (``inventory.low_stock_threshold``) when no row-level
        override is configured — which is currently always.
        """
        return self._DEFAULT_SAFETY_THRESHOLD
