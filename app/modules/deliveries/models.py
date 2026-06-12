from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class VehicleType(StrEnum):
    MOTORCYCLE = "motorcycle"
    BICYCLE = "bicycle"
    VAN = "van"
    OTHER = "other"


class RiderStatus(StrEnum):
    OFFLINE = "offline"
    AVAILABLE = "available"
    BUSY = "busy"


class Rider(Base, TimestampMixin):
    __tablename__ = "riders"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(16), nullable=False)
    license_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true"),
    )
    current_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=RiderStatus.OFFLINE.value,
    )

    __table_args__ = (
        CheckConstraint(
            "vehicle_type IN ('motorcycle','bicycle','van','other')",
            name="vehicle_type_enum",
        ),
        CheckConstraint(
            "current_status IN ('offline','available','busy')",
            name="current_status_enum",
        ),
        Index("ix_riders_is_active", "is_active"),
    )


class DeliveryAssignment(Base, TimestampMixin):
    __tablename__ = "delivery_assignments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    assigned_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    assigned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    picked_up_at: Mapped[datetime | None] = mapped_column(nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # COD reconciliation
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)
    cod_expected: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    cod_collected: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True,
    )
    cod_status: Mapped[str] = mapped_column(String(16), nullable=False)
    cod_reconciled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cod_reconciled_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    cod_resolution_notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # POD evidence
    pod_photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pod_signature_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pod_otp_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    pod_recipient_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    pod_notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    history: Mapped[list[DeliveryStatusHistory]] = relationship(
        "DeliveryStatusHistory",
        back_populates="assignment",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DeliveryStatusHistory.transitioned_at",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('assigned','picked_up','delivered','completed','cancelled','failed')",
            name="status_enum",
        ),
        CheckConstraint(
            "payment_method IN ('cod','online')",
            name="payment_method_enum",
        ),
        CheckConstraint(
            "cod_status IN ('n/a','pending','reconciled','discrepancy','resolved')",
            name="cod_status_enum",
        ),
        CheckConstraint("cod_expected >= 0", name="cod_expected_nonneg"),
        CheckConstraint(
            "cod_collected IS NULL OR cod_collected >= 0",
            name="cod_collected_nonneg",
        ),
        Index("ix_delivery_assignments_order_id", "order_id"),
        Index("ix_delivery_assignments_rider_id", "rider_id"),
        Index("ix_delivery_assignments_status", "status"),
    )


class DeliveryStatusHistory(Base):
    """Append-only transition log. REVOKE UPDATE/DELETE applied at the
    migration level — same defence pattern as audit_log / stock_ledger /
    order_status_history.
    """

    __tablename__ = "delivery_status_history"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("delivery_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    transitioned_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    transitioned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    assignment: Mapped[DeliveryAssignment] = relationship(
        "DeliveryAssignment", back_populates="history",
    )

    __table_args__ = (
        CheckConstraint(
            "to_status IN ('assigned','picked_up','delivered','completed','cancelled','failed')",
            name="to_status_enum",
        ),
        Index(
            "ix_delivery_status_history_assignment",
            "assignment_id",
            "transitioned_at",
        ),
    )
