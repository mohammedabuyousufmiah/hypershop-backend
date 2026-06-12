"""Packing ORM models.

Three tables:

- ``packing_sessions`` — header per order. Partial unique on
  ``WHERE status = 'open'`` so an order can have at most one open session
  but cancelled sessions don't block re-opening.
- ``packing_session_lines`` — expected items snapshotted at session open
  from the order's reserved-batch ledger. ``expected_batch_id`` is the
  FEFO-chosen batch from the inventory orchestrator; the picker is
  expected to scan from this batch (or supervisor-override).
- ``packing_scans`` — append-only audit ledger of every scan attempt
  (accepted + rejected). REVOKE UPDATE/DELETE applied at the migration
  level, mirroring stock_ledger / order_status_history.
"""

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


class PackingSession(Base, TimestampMixin):
    __tablename__ = "packing_sessions"

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
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    opened_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    opened_by: Mapped[UUID | None] = mapped_column(
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
    cancellation_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    lines: Mapped[list[PackingSessionLine]] = relationship(
        "PackingSessionLine",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PackingSessionLine.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','completed','cancelled')",
            name="status_enum",
        ),
        Index("ix_packing_sessions_order_id", "order_id"),
        Index("ix_packing_sessions_status", "status"),
    )


class PackingSessionLine(Base, TimestampMixin):
    __tablename__ = "packing_session_lines"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("packing_sessions.id", ondelete="CASCADE"),
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
    expected_batch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    expected_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    scanned_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # If the supervisor approved scanning a different batch, we track which:
    accepted_batch_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="RESTRICT"),
        nullable=True,
    )

    session: Mapped[PackingSession] = relationship(
        "PackingSession", back_populates="lines",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','complete','overridden')",
            name="status_enum",
        ),
        CheckConstraint("expected_quantity > 0", name="expected_qty_positive"),
        CheckConstraint(
            "scanned_quantity >= 0 AND scanned_quantity <= expected_quantity",
            name="scanned_qty_in_bounds",
        ),
        Index("ix_packing_lines_session_id", "session_id"),
        Index("ix_packing_lines_variant_id", "variant_id"),
    )


class PackingScan(Base):
    """Append-only ledger of every scan attempt — accepted or rejected.

    REVOKE UPDATE/DELETE applied via migration. This is what auditors look
    at to check for wrong-item rates, supervisor-override frequency, and
    expired-stock attempts at the packing line.
    """

    __tablename__ = "packing_scans"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("packing_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_line_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("packing_session_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    scanned_barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    matched_variant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    scanned_batch_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    is_supervisor_override: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false"),
    )
    supervisor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    scanned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    scanned_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted','wrong_item','expired','batch_mismatch',"
            "'overridden','over_quantity','unknown_barcode')",
            name="outcome_enum",
        ),
        Index("ix_packing_scans_session_id", "session_id", "scanned_at"),
        Index("ix_packing_scans_outcome", "outcome"),
    )
