from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class ReturnRequest(Base, TimestampMixin):
    __tablename__ = "return_requests"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    customer_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(2048), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    requested_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    received_at: Mapped[datetime | None] = mapped_column(nullable=True)
    received_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    inspected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    inspected_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)

    lines: Mapped[list[ReturnRequestLine]] = relationship(
        "ReturnRequestLine",
        back_populates="request",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReturnRequestLine.created_at",
    )
    history: Mapped[list[ReturnStatusHistory]] = relationship(
        "ReturnStatusHistory",
        back_populates="request",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReturnStatusHistory.transitioned_at",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','received','inspected','completed',"
            "'rejected','cancelled')",
            name="status_enum",
        ),
        Index("ix_return_requests_order_id", "order_id"),
        Index("ix_return_requests_customer_user_id", "customer_user_id"),
        Index("ix_return_requests_status", "status"),
    )


class ReturnRequestLine(Base, TimestampMixin):
    __tablename__ = "return_request_lines"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    return_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("return_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_line_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("order_lines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Snapshot at request time for stable history.
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    variant_sku: Mapped[str] = mapped_column(String(64), nullable=False)

    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    inspected_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Set at inspection time:
    condition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_batch_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_warehouse_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
    )
    inspection_notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Set at completion when inventory movement is applied:
    applied_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(nullable=True)

    request: Mapped[ReturnRequest] = relationship(
        "ReturnRequest", back_populates="lines",
    )

    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="requested_qty_positive"),
        CheckConstraint(
            "inspected_quantity IS NULL OR inspected_quantity >= 0",
            name="inspected_qty_nonneg",
        ),
        CheckConstraint(
            "condition IS NULL OR condition IN "
            "('sealed','opened','cold_chain_broken','expired')",
            name="condition_enum",
        ),
        CheckConstraint(
            "applied_action IS NULL OR applied_action IN "
            "('restock','block','dispose')",
            name="applied_action_enum",
        ),
        Index("ix_return_request_lines_request_id", "return_request_id"),
        Index("ix_return_request_lines_variant_id", "variant_id"),
    )


class ReturnStatusHistory(Base):
    """Append-only transition log. REVOKE UPDATE/DELETE applied via
    migration — same defence pattern as audit_log / order_status_history.
    """

    __tablename__ = "return_status_history"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    return_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("return_requests.id", ondelete="CASCADE"),
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

    request: Mapped[ReturnRequest] = relationship(
        "ReturnRequest", back_populates="history",
    )

    __table_args__ = (
        CheckConstraint(
            "to_status IN ('requested','received','inspected','completed',"
            "'rejected','cancelled')",
            name="to_status_enum",
        ),
        Index(
            "ix_return_status_history_request",
            "return_request_id",
            "transitioned_at",
        ),
    )
