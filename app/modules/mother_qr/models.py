"""ORM models for the Mother-QR workflow."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class MotherQrItem(Base, TimestampMixin):
    """One physical receiving unit. Carries the canonical status and
    location_code. Transitions enforced by ``service.apply_scan``."""

    __tablename__ = "mother_qr_items"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    mother_qr: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    receiving_batch_qr: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False,
                                           server_default="1")
    status: Mapped[str] = mapped_column(String(40), nullable=False,
                                         server_default="GATE_IN")
    location_code: Mapped[str] = mapped_column(String(80), nullable=False)
    location_hierarchy_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warehouse_id: Mapped[str] = mapped_column(String(80), nullable=False)
    received_by: Mapped[str] = mapped_column(String(80), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    supplier_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    purchase_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    batch_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    serial_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expiry_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_mother_qr_items_qty_pos"),
        Index("ix_mother_qr_items_sku", "sku", "warehouse_id"),
        Index("ix_mother_qr_items_status", "status"),
        Index("ix_mother_qr_items_order", "order_id"),
        Index("ix_mother_qr_items_supplier", "supplier_id"),
    )


class MotherQrScanEvent(Base):
    """Append-only scan log. One row per attempted state transition."""

    __tablename__ = "mother_qr_scan_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    event_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    mother_qr: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_status: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(48), nullable=False)
    location_code: Mapped[str] = mapped_column(String(80), nullable=False)
    scanned_qr: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scan_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False,
                                         server_default="ok")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index("ix_mqr_scan_events_mqr", "mother_qr", "created_at"),
        Index("ix_mqr_scan_events_action", "action", "created_at"),
        Index("ix_mqr_scan_events_actor", "actor_id", "created_at"),
        Index("ix_mqr_scan_events_order", "order_id"),
    )


class WarehouseLocation(Base):
    """Registered Shelf/Rack/Bin QR codes per warehouse."""

    __tablename__ = "warehouse_locations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    qr_code: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    warehouse_id: Mapped[str] = mapped_column(String(80), nullable=False)
    zone: Mapped[str] = mapped_column(String(40), nullable=False)
    aisle: Mapped[str] = mapped_column(String(40), nullable=False)
    rack: Mapped[str] = mapped_column(String(40), nullable=False)
    shelf: Mapped[str] = mapped_column(String(40), nullable=False)
    bin: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index("ix_warehouse_locations_wh_active", "warehouse_id", "is_active"),
    )
