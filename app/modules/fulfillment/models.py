"""Marketplace dispatch domain ORM models (migration 0081).

11 tables that back the Marketplace Fulfillment hub with real persistent
rows instead of derived views over orders/courier_shipments.

All names prefixed ``Marketplace*`` to keep the namespace scoped — the
core ``orders`` module owns the legal Order entity; this module owns the
operational dispatch lifecycle around it.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


# Re-used CHECK lists — sourced from migration 0081 verbatim.
_TASK_TYPES = "('pickup','delivery','return')"
# 0082 widened to include 'on_hold' + renamed status -> dispatch_status.
_DISPATCH_STATUSES = (
    "('pending','assigned','in_progress','completed','failed','cancelled','on_hold')"
)
_PRIORITY_LEVELS = "('low','medium','high','critical')"
_DELIVERY_TYPES = "('standard','express','same_day','next_day','scheduled','economy')"
_PAYMENT_STATUSES = "('pending','verified','cod_accepted','refunded','failed','partial')"
_RISK_LEVELS = "('low','medium','high','critical')"
_PICKUP_STATUSES = "('assigned','accepted','picked_up','failed','cancelled')"
_DELIVERY_STATUSES = (
    "('assigned','in_transit','delivered','failed','cancelled')"
)
_ATTEMPT_OUTCOMES = (
    "('delivered','failed','rescheduled','customer_unreachable',"
    "'address_issue','cod_refused','partial')"
)
_MOVEMENT_LEGS = (
    "('to_hub','at_hub','to_seller','with_seller','seller_signed')"
)
_BATCH_TYPES = "('pickup','delivery','return')"
_BATCH_STATUSES = "('pending','active','completed','cancelled')"
_VEHICLE_TYPES = "('bicycle','motorbike','van','truck','foot')"
_COD_ACTIONS = "('none','flagged','blocked','escalated','cleared')"
_SLA_TYPES = "('dispatch','pickup','delivery','return','seller_pack')"
_SLA_SEVERITIES = "('warning','critical','escalated')"
_ESC_LEVELS = (
    "('supervisor','finance','manager','super_admin',"
    "'rider_manager','seller_manager')"
)
_ESC_STATUSES = "('open','in_progress','resolved','dismissed')"


class MarketplaceDispatchTask(Base):
    """Central dispatch work queue — one row per pickup/delivery/return
    task. Drives the Dispatch Dashboard + auto-assignment cron."""

    __tablename__ = "marketplace_dispatch_tasks"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    # 14-field canonical spec (0082) + back-compat extras.
    seller_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    zone_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    task_type: Mapped[str] = mapped_column(String(20), nullable=False)
    delivery_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'standard'"),
    )
    current_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    dispatch_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"),
    )
    priority_level: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'medium'"),
    )
    risk_level: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'low'"),
    )
    payment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"),
    )
    cod_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    assigned_rider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    sla_deadline: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )

    __table_args__ = (
        CheckConstraint(f"task_type IN {_TASK_TYPES}", name="task_type_enum"),
        CheckConstraint(
            f"dispatch_status IN {_DISPATCH_STATUSES}",
            name="dispatch_status_enum",
        ),
        CheckConstraint(
            f"priority_level IN {_PRIORITY_LEVELS}",
            name="priority_level_enum",
        ),
        CheckConstraint(
            f"delivery_type IN {_DELIVERY_TYPES}", name="delivery_type_enum",
        ),
        CheckConstraint(
            f"payment_status IN {_PAYMENT_STATUSES}",
            name="payment_status_enum",
        ),
        CheckConstraint(
            f"risk_level IN {_RISK_LEVELS}", name="risk_level_enum",
        ),
        CheckConstraint(
            "cod_amount IS NULL OR cod_amount >= 0", name="cod_amount_nonneg",
        ),
        Index("ix_dt_status_priority", "dispatch_status", "priority_level"),
        Index("ix_dt_order", "order_id"),
        Index("ix_dt_rider", "assigned_rider_id"),
        Index("ix_dt_sla_deadline", "sla_deadline"),
        Index("ix_dt_seller", "seller_id"),
        Index("ix_dt_customer", "customer_id"),
        Index("ix_dt_zone_status", "zone_id", "dispatch_status"),
        Index("ix_dt_payment_status", "payment_status"),
        Index("ix_dt_risk_status", "risk_level", "dispatch_status"),
    )


class MarketplacePickupAssignment(Base):
    __tablename__ = "marketplace_pickup_assignments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_dispatch_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    rider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    seller_hub: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # 10-field canonical spec (0083) ---
    assigned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    assignment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pickup'"),
    )
    reassignment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    reassignment_reason: Mapped[str | None] = mapped_column(
        String(400), nullable=True,
    )
    proof_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    # --- end spec ---
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    picked_up_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    scan_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'assigned'"),
    )
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"status IN {_PICKUP_STATUSES}", name="pickup_status_enum",
        ),
        CheckConstraint(
            "assignment_type IN ('pickup','delivery','return')",
            name="marketplace_pickup_assignments_type_enum",
        ),
        CheckConstraint(
            "reassignment_count >= 0",
            name="marketplace_pickup_assignments_reass_nonneg",
        ),
        Index("ix_pickup_rider_status", "rider_id", "status"),
        Index("ix_pickup_order", "order_id"),
        Index("ix_pickup_assigned_by", "assigned_by"),
        Index("ix_pickup_reass_count", "reassignment_count"),
    )


class MarketplaceDeliveryAssignment(Base):
    __tablename__ = "marketplace_delivery_assignments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_dispatch_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    rider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    batch_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_dispatch_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 10-field canonical spec (0083) ---
    assigned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    assignment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'delivery'"),
    )
    reassignment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    reassignment_reason: Mapped[str | None] = mapped_column(
        String(400), nullable=True,
    )
    proof_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    # --- end spec ---
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'assigned'"),
    )
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"status IN {_DELIVERY_STATUSES}", name="delivery_status_enum",
        ),
        CheckConstraint(
            "assignment_type IN ('pickup','delivery','return')",
            name="marketplace_delivery_assignments_type_enum",
        ),
        CheckConstraint(
            "reassignment_count >= 0",
            name="marketplace_delivery_assignments_reass_nonneg",
        ),
        Index("ix_del_rider_status", "rider_id", "status"),
        Index("ix_del_order", "order_id"),
        Index("ix_del_batch", "batch_id"),
        Index("ix_del_assigned_by", "assigned_by"),
        Index("ix_del_reass_count", "reassignment_count"),
    )


class MarketplaceDeliveryAttempt(Base):
    __tablename__ = "marketplace_delivery_attempts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    rider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 12-field canonical spec (0084) — created_at replaces attempted_at.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    attempt_status: Mapped[str] = mapped_column(String(20), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    customer_contacted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    call_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    proof_photo_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    gps_location: Mapped[str | None] = mapped_column(
        String(40), nullable=True,
    )
    note: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # --- back-compat extras (not in spec but kept for ops detail) ---
    signature_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    cod_collected_minor: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    gps_lat: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True,
    )
    gps_lng: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            f"attempt_status IN {_ATTEMPT_OUTCOMES}",
            name="attempt_status_enum",
        ),
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        CheckConstraint(
            "call_attempt_count >= 0", name="call_attempt_count_nonneg",
        ),
        UniqueConstraint("order_id", "attempt_no", name="uq_order_attempt"),
        Index("ix_attempts_order_time", "order_id", "created_at"),
        Index("ix_attempts_rider", "rider_id"),
        Index(
            "ix_attempts_contact_status",
            "customer_contacted", "attempt_status",
        ),
    )


class MarketplaceReturnMovement(Base):
    __tablename__ = "marketplace_return_movements"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    return_request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    leg: Mapped[str] = mapped_column(String(20), nullable=False)
    from_location: Mapped[str | None] = mapped_column(
        String(120), nullable=True,
    )
    to_location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    moved_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    moved_by_rider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    scan_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    signature_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    seller_otp: Mapped[str | None] = mapped_column(String(12), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(400), nullable=True)

    __table_args__ = (
        CheckConstraint(f"leg IN {_MOVEMENT_LEGS}", name="movement_leg_enum"),
        Index("ix_rm_order_time", "order_id", "moved_at"),
        Index("ix_rm_return", "return_request_id"),
    )


class MarketplaceDispatchBatch(Base):
    __tablename__ = "marketplace_dispatch_batches"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    batch_code: Mapped[str] = mapped_column(
        String(40), nullable=False, unique=True,
    )
    rider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    batch_type: Mapped[str] = mapped_column(String(20), nullable=False)
    hub_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    order_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"),
    )
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"batch_type IN {_BATCH_TYPES}", name="batch_type_enum",
        ),
        CheckConstraint(
            f"status IN {_BATCH_STATUSES}", name="batch_status_enum",
        ),
        CheckConstraint(
            "order_count >= 0", name="batch_order_count_nonneg",
        ),
        Index("ix_batch_rider_status", "rider_id", "status"),
        Index("ix_batch_type_status", "batch_type", "status"),
    )


class MarketplaceRiderCapacity(Base):
    """One row per rider — capacity snapshot driven by dispatch service.

    PK is rider_id (no surrogate id). Cron rebuilds active_* counts
    periodically; mobile rider app pushes last_location_*. COD float
    cap gates pickup assignment (rider can't take more if cap reached).
    """

    __tablename__ = "marketplace_rider_capacity"

    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    active_pickup_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    active_delivery_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    max_pickup: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("10"),
    )
    max_delivery: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("20"),
    )
    cod_float_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    max_cod_float_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("5000000"),
    )
    vehicle_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_location_lat: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True,
    )
    last_location_lng: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True,
    )
    available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    # 0086 — operator override; True = refuse all new COD assignments
    cod_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    last_update_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            f"vehicle_type IS NULL OR vehicle_type IN {_VEHICLE_TYPES}",
            name="vehicle_type_enum",
        ),
        CheckConstraint(
            "active_pickup_count >= 0 AND active_delivery_count >= 0 "
            "AND cod_float_minor >= 0",
            name="rider_cap_nonneg",
        ),
        Index("ix_rc_available", "available"),
    )


class MarketplaceCodRiskLog(Base):
    __tablename__ = "marketplace_cod_risk_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    rider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_factors: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    action_taken: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'none'"),
    )
    flagged_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    resolved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(String(400), nullable=True)

    __table_args__ = (
        CheckConstraint(f"action_taken IN {_COD_ACTIONS}", name="cod_action_enum"),
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100", name="cod_score_range",
        ),
        Index("ix_cod_order", "order_id"),
        Index("ix_cod_score_time", "risk_score", "flagged_at"),
    )


class MarketplaceSlaAlert(Base):
    __tablename__ = "marketplace_sla_alerts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    sla_type: Mapped[str] = mapped_column(String(20), nullable=False)
    breach_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    raised_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    resolved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    resolution_action: Mapped[str | None] = mapped_column(
        String(40), nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(String(400), nullable=True)

    __table_args__ = (
        CheckConstraint(f"sla_type IN {_SLA_TYPES}", name="sla_type_enum"),
        CheckConstraint(
            f"severity IN {_SLA_SEVERITIES}", name="sla_severity_enum",
        ),
        CheckConstraint("breach_minutes >= 0", name="sla_breach_nonneg"),
        Index("ix_sla_order_time", "order_id", "raised_at"),
        # Partial index built by migration (postgresql_where) — ORM
        # doesn't need to re-declare; alembic owns the partial.
    )


class MarketplaceDispatchEscalation(Base):
    __tablename__ = "marketplace_dispatch_escalations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    escalation_level: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(400), nullable=False)
    escalated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    escalated_to: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'open'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    resolution_notes: Mapped[str | None] = mapped_column(
        String(400), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            f"escalation_level IN {_ESC_LEVELS}", name="esc_level_enum",
        ),
        CheckConstraint(
            f"status IN {_ESC_STATUSES}", name="esc_status_enum",
        ),
        Index("ix_esc_status", "status"),
        Index("ix_esc_order_time", "order_id", "created_at"),
    )


class MarketplaceRiderCodHolding(Base):
    """Per-event COD exposure log (migration 0085).

    Append-only. One row written each time a COD order is assigned to a
    rider, capturing the rider's running holding + cap snapshot so
    finance can reconstruct exposure history even when the cap or
    holding is later mutated. ``blocked_from_cod_assignment=True`` rows
    record cap-breach attempts that the assignment service rejected.
    """

    __tablename__ = "marketplace_rider_cod_holdings"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    cod_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_cod_holding: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    cod_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    risk_status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'safe'"),
    )
    blocked_from_cod_assignment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "risk_status IN ('safe','watch','high','blocked')",
            name="risk_status_enum",
        ),
        CheckConstraint(
            "cod_amount >= 0 AND current_cod_holding >= 0 AND cod_limit >= 0",
            name="cod_amounts_nonneg",
        ),
        Index("ix_rch_rider_time", "rider_id", "created_at"),
        Index("ix_rch_order", "order_id"),
        Index("ix_rch_risk", "risk_status"),
        # Partial idx on blocked rows declared in migration via
        # postgresql_where — ORM-side full idx covers fallback queries.
    )


class MarketplaceOrderAuditLog(Base):
    """Per-order 10-field strict audit trail (migration 0088).

    Separate from generic ``marketplace_dispatch_audit_logs``: every row
    is scoped to one order, captures actor_role snapshot, and stores
    old_value/new_value as typed JSONB for diff visualisation.
    Append-only — REVOKE UPDATE/DELETE.
    """

    __tablename__ = "marketplace_order_audit_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    actor_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    proof_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index("ix_oal_order_time", "order_id", "created_at"),
        Index("ix_oal_actor_time", "actor_id", "created_at"),
        Index("ix_oal_action_time", "action_type", "created_at"),
    )


class MarketplaceSellerPenalty(Base):
    """Auto-applied seller performance penalties (migration 0087).

    Written by the seller-penalty-sweep cron when performance metric
    breaches a threshold. Operator can ``cleared_at`` to lift.
    """

    __tablename__ = "marketplace_seller_penalties"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    seller_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    penalty_type: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'warning'"),
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    applied_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
    )
    cleared_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    clear_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "penalty_type IN ('rank_down','warning','account_review',"
            "'order_limit','commission_cut','listing_freeze')",
            name="penalty_type_enum",
        ),
        CheckConstraint(
            "severity IN ('warning','critical','blocking')",
            name="penalty_severity_enum",
        ),
        Index("ix_sp_seller_time", "seller_id", "applied_at"),
    )


class MarketplaceDispatchAuditLog(Base):
    """Append-only audit. Migration REVOKEs UPDATE/DELETE from PUBLIC."""

    __tablename__ = "marketplace_dispatch_audit_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index("ix_dal_actor_time", "actor_user_id", "created_at"),
        Index("ix_dal_resource", "resource_type", "resource_id"),
        Index("ix_dal_action_time", "action", "created_at"),
    )
