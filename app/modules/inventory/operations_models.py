"""ORM models for the Inventory Manager operational queues."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class InventoryAuditLog(Base):
    """Append-only Inventory Manager action audit trail."""

    __tablename__ = "inventory_audit_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    audit_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(48), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(80), nullable=True)
    warehouse_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    qty_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qty_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qty_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_status: Mapped[str | None] = mapped_column(String(48), nullable=True)
    new_status: Mapped[str] = mapped_column(String(48), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index("ix_inv_audit_entity", "entity_type", "entity_id"),
        Index("ix_inv_audit_sku", "sku"),
        Index("ix_inv_audit_action", "action"),
    )


class InventoryStock(Base, TimestampMixin):
    __tablename__ = "inventory_stocks"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(80), nullable=False)
    available_qty: Mapped[int] = mapped_column(Integer, nullable=False,
                                                server_default="0")
    reserved_qty: Mapped[int] = mapped_column(Integer, nullable=False,
                                               server_default="0")
    damaged_qty: Mapped[int] = mapped_column(Integer, nullable=False,
                                              server_default="0")
    lost_qty: Mapped[int] = mapped_column(Integer, nullable=False,
                                           server_default="0")
    quarantine_qty: Mapped[int] = mapped_column(Integer, nullable=False,
                                                 server_default="0")
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False,
                                                      server_default="0")
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                              server_default=text("false"))
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_movement_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("sku", "warehouse_id", name="uq_inventory_stocks_sku_wh"),
        CheckConstraint("available_qty >= 0", name="ck_inv_stocks_avail_nonneg"),
        CheckConstraint("reserved_qty >= 0", name="ck_inv_stocks_reserved_nonneg"),
        Index("ix_inventory_stocks_sku", "sku"),
        Index("ix_inventory_stocks_warehouse", "warehouse_id"),
        Index("ix_inventory_stocks_blocked", "is_blocked"),
    )


class StockReservation(Base, TimestampMixin):
    __tablename__ = "stock_reservations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    reservation_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    order_id: Mapped[str] = mapped_column(String(80), nullable=False)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(80), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False,
                                         server_default="active")
    reserved_by: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','released','consumed','expired')",
                        name="ck_stock_reservations_status"),
        CheckConstraint("qty > 0", name="ck_stock_reservations_qty_pos"),
        Index("ix_stock_reservations_order", "order_id"),
        Index("ix_stock_reservations_sku", "sku", "warehouse_id"),
        Index("ix_stock_reservations_status", "status"),
    )


class StockMovement(Base):
    """Append-only stock movement ledger."""

    __tablename__ = "stock_movements"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    movement_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(80), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(32), nullable=False)
    qty_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_after: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(48), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "movement_type IN ('receive','issue','reserve','release',"
            "'adjust_in','adjust_out','damage','loss','return_in',"
            "'quarantine_in','quarantine_out')",
            name="ck_stock_movements_type",
        ),
        Index("ix_stock_movements_sku", "sku", "warehouse_id"),
        Index("ix_stock_movements_created", "created_at"),
    )


class StockAdjustmentRequest(Base, TimestampMixin):
    __tablename__ = "stock_adjustment_requests"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    request_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    qty_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_before: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False,
                                         server_default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("direction IN ('in','out')", name="ck_stock_adj_direction"),
        CheckConstraint("status IN ('pending','approved','rejected')",
                        name="ck_stock_adj_status"),
        CheckConstraint("qty_delta != 0", name="ck_stock_adj_delta_nonzero"),
        Index("ix_stock_adj_sku", "sku", "warehouse_id"),
        Index("ix_stock_adj_status", "status"),
    )


class ReturnStockReview(Base, TimestampMixin):
    __tablename__ = "return_stock_reviews"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    review_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    order_id: Mapped[str] = mapped_column(String(80), nullable=False)
    return_id: Mapped[str] = mapped_column(String(80), nullable=False)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(80), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    qc_status: Mapped[str] = mapped_column(String(24), nullable=False,
                                            server_default="pending")
    qc_pass_evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    shelf_qr: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mother_qr_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False,
                                         server_default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("qc_status IN ('pending','qc_passed','qc_failed')",
                        name="ck_return_review_qc"),
        CheckConstraint("status IN ('pending','approved','rejected')",
                        name="ck_return_review_status"),
        CheckConstraint("qty > 0", name="ck_return_review_qty_pos"),
        Index("ix_return_review_return", "return_id"),
        Index("ix_return_review_status", "status"),
    )


class DamagedLostInventory(Base, TimestampMixin):
    __tablename__ = "damaged_lost_inventory"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    incident_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(80), nullable=False)
    incident_type: Mapped[str] = mapped_column(String(24), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    responsible_party: Mapped[str | None] = mapped_column(String(48), nullable=True)
    source_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_movement_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str] = mapped_column(String(512), nullable=False)
    reported_by: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False,
                                         server_default="pending")
    confirmed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    financial_impact_minor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "incident_type IN ('damaged','lost','expired','theft')",
            name="ck_damaged_lost_type",
        ),
        CheckConstraint(
            "status IN ('pending','confirmed','resolved','written_off')",
            name="ck_damaged_lost_status",
        ),
        CheckConstraint("qty > 0", name="ck_damaged_lost_qty_pos"),
        Index("ix_damaged_lost_sku", "sku", "warehouse_id"),
        Index("ix_damaged_lost_status", "status"),
        Index("ix_damaged_lost_type", "incident_type"),
    )


class SellerStockAccuracy(Base, TimestampMixin):
    __tablename__ = "seller_stock_accuracy"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    seller_id: Mapped[str] = mapped_column(String(80), nullable=False)
    scored_at: Mapped[date] = mapped_column(Date, nullable=False)
    listed_qty: Mapped[int] = mapped_column(Integer, nullable=False,
                                             server_default="0")
    verified_qty: Mapped[int] = mapped_column(Integer, nullable=False,
                                               server_default="0")
    oversold_count_30d: Mapped[int] = mapped_column(Integer, nullable=False,
                                                     server_default="0")
    missing_count_30d: Mapped[int] = mapped_column(Integer, nullable=False,
                                                    server_default="0")
    accuracy_bps: Mapped[int] = mapped_column(Integer, nullable=False,
                                               server_default="10000")
    audit_requested: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                   server_default=text("false"))
    audit_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    audit_requested_by: Mapped[str | None] = mapped_column(String(80), nullable=True)

    __table_args__ = (
        UniqueConstraint("seller_id", "scored_at", name="uq_seller_accuracy_day"),
        CheckConstraint("accuracy_bps BETWEEN 0 AND 10000",
                        name="ck_seller_accuracy_bps_range"),
        Index("ix_seller_accuracy_seller", "seller_id"),
    )
