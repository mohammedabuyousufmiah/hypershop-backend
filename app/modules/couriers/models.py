"""Couriers ORM mappings — mirror alembic migration 0069 column shapes.

5 tables, all prefixed ``hypershop_courier_``. Money columns are
BIGINT minor (paisa). Provider codes are lowercase string keys
matching the python ``CourierProvider.code`` attribute.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class CourierProvider(Base):
    __tablename__ = "hypershop_courier_providers"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default=text("'BD'"),
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    supports_cod: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    supports_pickup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    supports_return: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    coverage_zones: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )


class CourierCredential(Base):
    __tablename__ = "hypershop_courier_credentials"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    provider_code: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("hypershop_courier_providers.code", ondelete="CASCADE"),
        nullable=False,
    )
    environment: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'sandbox'"),
    )
    base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    merchant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extra_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "environment IN ('sandbox','production')",
            name="ck_hypershop_courier_creds_env",
        ),
        Index(
            "ix_hypershop_courier_creds_provider_active",
            "provider_code", "is_active",
        ),
    )


class CourierShipment(Base):
    __tablename__ = "hypershop_courier_shipments"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False,
    )
    provider_code: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("hypershop_courier_providers.code"),
        nullable=False,
    )
    provider_shipment_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    tracking_number: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'created'"),
    )
    service_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'regular'"),
    )
    is_cod: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    cod_amount_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    shipping_charge_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    label_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pickup_address: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    delivery_address: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('created','pickup_pending','in_transit','out_for_delivery',"
            "'delivered','returned','cancelled','failed','exception')",
            name="ck_hypershop_courier_shipment_status",
        ),
        CheckConstraint(
            "service_type IN ('regular','express','same_day','next_day')",
            name="ck_hypershop_courier_shipment_service",
        ),
        CheckConstraint(
            "cod_amount_minor >= 0",
            name="ck_hypershop_courier_shipment_cod_nonneg",
        ),
        Index(
            "ix_hypershop_courier_shipments_order",
            "order_id",
        ),
        Index(
            "ix_hypershop_courier_shipments_provider_id",
            "provider_code", "provider_shipment_id",
            unique=True,
            postgresql_where=text("provider_shipment_id IS NOT NULL"),
        ),
        Index(
            "ix_hypershop_courier_shipments_status",
            "status", "created_at",
        ),
    )


class CourierStatusEvent(Base):
    __tablename__ = "hypershop_courier_status_events"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    shipment_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_courier_shipments.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_shipment_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mapped_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index(
            "ix_hypershop_courier_status_shipment",
            "shipment_id", text("received_at DESC"),
        ),
    )


class CourierCodRemittance(Base):
    __tablename__ = "hypershop_courier_cod_remittances"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    shipment_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_courier_shipments.id"),
        nullable=False,
    )
    cod_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    courier_fee_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    settled_amount_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    settlement_reference: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'"),
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','settled','disputed','reconciled')",
            name="ck_hypershop_courier_cod_remit_status",
        ),
        Index(
            "ix_hypershop_courier_cod_remit_provider",
            "provider_code", "status",
        ),
    )


__all__ = [
    "CourierProvider",
    "CourierCredential",
    "CourierShipment",
    "CourierStatusEvent",
    "CourierCodRemittance",
]
