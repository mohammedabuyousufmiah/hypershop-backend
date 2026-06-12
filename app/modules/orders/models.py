"""Orders ORM models.

- ``orders`` — header row, owns the state machine status.
- ``order_lines`` — per-variant lines snapshot (price, name, Rx flag) so the
  order remains accurate even if the product is later edited.
- ``order_status_history`` — append-only state-change log (mirrors the
  audit_log + stock_ledger defence-in-depth pattern: REVOKE UPDATE/DELETE).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class PaymentMethod(StrEnum):
    COD = "cod"  # cash on delivery — auto-confirms at placement
    ONLINE = "online"  # any gateway-backed method


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    customer_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # 21-state fulfillment sub-status (see ``state.OrderFulfillmentStage``).
    # Persisted by migration 0080. Defaults to the backfilled value for
    # pre-existing rows; new orders should set it via the service helper.
    fulfillment_stage: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'ORDER_PLACED'"),
    )
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)
    requires_prescription: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("false"),
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    shipping_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    discount_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0"),
    )
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    delivery_address: Mapped[dict] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    placed_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    payment_confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    assigned_pharmacist_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    lines: Mapped[list[OrderLine]] = relationship(
        "OrderLine",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    history: Mapped[list[OrderStatusHistory]] = relationship(
        "OrderStatusHistory",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderStatusHistory.transitioned_at",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_payment','payment_confirmed','stock_reserved',"
            "'approved','packing','out_for_delivery',"
            "'completed','cancelled','failed')",
            name="status_enum",
        ),
        CheckConstraint(
            "payment_method IN ('cod','online')",
            name="payment_method_enum",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="currency_iso",
        ),
        CheckConstraint("subtotal >= 0", name="subtotal_nonneg"),
        CheckConstraint("grand_total >= 0", name="grand_total_nonneg"),
        Index("ix_orders_customer_status", "customer_user_id", "status"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_placed_at", "placed_at"),
    )

    # ------------------------------------------------------------------
    # Dashboard protocol adapters (read-only).
    #
    # These ``@hybrid_property`` views let an Order instance satisfy the
    # ``app.modules.dashboard.protocols.IDashboardOrder`` structural
    # protocol without renaming existing columns or breaking downstream
    # callers. Aggregators in
    # ``app/modules/dashboard/aggregators.py`` (e.g. ``order_kpi_summary``)
    # can then consume Order rows directly via the protocol.
    #
    # Caveats:
    #   * ``net_profit`` and ``customer_satisfaction`` are NOT yet
    #     persisted on Hypershop's order schema — they default to 0.0
    #     until cost-of-goods + CSAT survey tables ship. Aggregators
    #     using these will return zeros, not raise — the protocol is
    #     still satisfied.
    #   * ``id`` is the existing UUID column (protocol declares ``int``
    #     for cross-system portability; runtime check only verifies
    #     attribute presence, not type, so UUID passes).
    #   * These are plain ``@property`` (not ``hybrid_property``)
    #     because the aggregators only need instance-level access; a
    #     hybrid_property's class-level expression path would try to
    #     wrap ``float()`` around an ``InstrumentedAttribute`` and
    #     break at import time. Promote to hybrid_property only if a
    #     future caller needs ``select(Order).where(Order.gmv > 1000)``.
    # ------------------------------------------------------------------
    @property
    def gmv(self) -> float:
        """Gross merchandise value — the order's total before refunds."""
        return float(self.grand_total)

    @property
    def net_revenue(self) -> float:
        """Revenue net of discount lines (still gross of taxes + shipping
        out, since those are pass-through, not Hypershop revenue)."""
        return float(self.subtotal - self.discount_total)

    @property
    def net_profit(self) -> float:
        """Profit after COGS. Stub returning 0.0 until per-line cost
        tracking lands — see ``finance`` module roadmap."""
        return 0.0

    @property
    def customer_satisfaction(self) -> float:
        """CSAT score 0..5. Stub returning 0.0 until the post-delivery
        CSAT survey table ships — see ``reviews`` module phase 2."""
        return 0.0


class OrderLine(Base, TimestampMixin):
    __tablename__ = "order_lines"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Snapshot of product/variant data at checkout — survives later edits.
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    variant_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    requires_prescription: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("false"),
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    order: Mapped[Order] = relationship("Order", back_populates="lines")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_nonneg"),
        CheckConstraint("line_total >= 0", name="line_total_nonneg"),
        Index("ix_order_lines_order_id", "order_id"),
        Index("ix_order_lines_variant_id", "variant_id"),
    )


class OrderStatusHistory(Base):
    """Append-only log of every status transition.

    The migration REVOKEs UPDATE/DELETE from PUBLIC so a service bug cannot
    rewrite history. Same defence pattern as ``audit_log`` and ``stock_ledger``.
    """

    __tablename__ = "order_status_history"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    transitioned_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    transitioned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    order: Mapped[Order] = relationship("Order", back_populates="history")

    __table_args__ = (
        CheckConstraint(
            "to_status IN ('pending_payment','payment_confirmed','stock_reserved',"
            "'prescription_review','approved','packing','out_for_delivery',"
            "'completed','cancelled','failed')",
            name="to_status_enum",
        ),
        Index("ix_order_status_history_order", "order_id", "transitioned_at"),
    )


_FS_VALUES = (
    "ORDER_PLACED,PAYMENT_PENDING,PAYMENT_VERIFIED,COD_ACCEPTED,"
    "SELLER_PROCESSING,READY_TO_SHIP,PICKUP_ASSIGNED,PICKED_UP,AT_HUB,"
    "SORTED_FOR_DELIVERY,RIDER_ASSIGNED,OUT_FOR_DELIVERY,DELIVERY_ATTEMPTED,"
    "DELIVERED,FAILED_DELIVERY,RESCHEDULED,RETURNING_TO_HUB,RETURNED_TO_HUB,"
    "RETURNING_TO_SELLER,RETURNED_TO_SELLER,CANCELLED,REFUNDED"
).split(",")
_FS_IN = ",".join(f"'{v}'" for v in _FS_VALUES)


# Mirror the migration's orders.fulfillment_stage CHECK at the ORM level so
# alembic --autogenerate doesn't try to drop it on the next revision.
Order.__table_args__ = (
    *Order.__table_args__,
    CheckConstraint(
        f"fulfillment_stage IN ({_FS_IN})", name="fulfillment_stage_enum",
    ),
    Index("ix_orders_fulfillment_stage", "fulfillment_stage"),
)


class OrderFulfillmentStageHistory(Base):
    """Append-only log of every fulfillment_stage transition.

    Parallel to ``order_status_history`` but for the 21-state ops machine.
    Migration 0080 REVOKEs UPDATE/DELETE from PUBLIC so a service bug cannot
    rewrite the log.
    """

    __tablename__ = "order_fulfillment_stage_history"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    transitioned_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    transitioned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"to_stage IN ({_FS_IN})", name="to_stage_enum",
        ),
        Index("ix_ofsh_order_time", "order_id", "transitioned_at"),
    )
