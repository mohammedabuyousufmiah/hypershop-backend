"""Event constants the finance module subscribes to.

Producers live in other modules (orders, returns, deliveries). Names
mirror the producer's published names — see each producer's events.py.
Payload schemas use ``extra='ignore'`` so producer evolution doesn't
break us.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# Subscribed event names (re-exported from producers).
EVT_ORDER_PAYMENT_CONFIRMED = "orders.order.payment_confirmed"
EVT_ORDER_COMPLETED = "orders.order.completed"
EVT_ORDER_CANCELLED = "orders.order.cancelled"
EVT_DELIVERY_COMPLETED = "deliveries.delivery.completed"
EVT_RETURN_COMPLETED = "returns.return.completed"
EVT_PAYMENT_RECONCILE_COMPLETED = "payment.reconciliation.completed"


class _ConsumerPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class OrderItemPayload(_ConsumerPayload):
    variant_id: UUID
    quantity: int = Field(..., ge=1, le=10_000_000)


class OrderPaymentConfirmedPayload(_ConsumerPayload):
    """The orders module emits this on payment_confirmed for both COD and
    online. Finance posts the revenue/AR JE off this.
    """

    order_id: UUID
    code: str | None = None
    customer_user_id: UUID | None = None
    items: list[OrderItemPayload] = Field(default_factory=list)


class OrderCompletedPayload(_ConsumerPayload):
    """Finance posts the COGS JE off this — looks up consumed batches via
    stock_ledger to compute cost.
    """

    order_id: UUID
    code: str | None = None
    completed_at: str | None = None


class OrderCancelledPayload(_ConsumerPayload):
    order_id: UUID
    reason: str | None = None


class DeliveryCompletedPayload(_ConsumerPayload):
    """Emitted by deliveries on completion. The producer's payload only
    carries identifiers; the handler looks up payment_method / cod_collected
    from the DeliveryAssignment row inside its own transaction. Keeps the
    deliveries module's event contract minimal and avoids stale data.
    """

    assignment_id: UUID
    order_id: UUID


class ReturnCompletedPayload(_ConsumerPayload):
    """Emitted by returns on COMPLETED. Refund amount is computed by the
    finance handler from the inspected line quantities × order_line
    unit_price (since the producer's payload only carries identifiers).
    """

    return_request_id: UUID
    order_id: UUID
    customer_user_id: UUID


class PaymentReconcileCompletedPayload(_ConsumerPayload):
    """Emitted by payments after a settlement report finishes reconciling.

    The handler posts ONE summary JournalEntry per report:
      - Clears AR-Gateway-Pending for matched expected_total
      - Credits Cash-in-Bank for actual submitted_total
      - Plugs Gateway-Drift for the delta

    Orphan lines (settled with no matching intent) are NOT auto-posted —
    they're logged and surface in the reconcile UI for manual handling.
    """

    report_id: UUID
    provider: str
    report_date: date
    currency: str = "BDT"
    matched_count: int = 0
    drift_count: int = 0
    orphan_count: int = 0
    drift_total: Decimal = Decimal("0")
    submitted_total: Decimal = Decimal("0")
    submitted_count: int = 0
