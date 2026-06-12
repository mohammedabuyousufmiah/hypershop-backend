"""Dashboard DTOs.

The dashboard module owns no tables — every metric is computed on demand
from the operational tables of other modules. These schemas are the wire
contract for the admin UI.

Filter rules
------------
- Date range filters are inclusive on both ends (``starts_on`` ≤
  date(occurred_at) ≤ ``ends_on``).
- Range queries default to "last 30 days" when omitted; absolute caps
  (``MAX_RANGE_DAYS``) prevent accidental full-table scans.
- ``warehouse_code`` filter is applied wherever the underlying table has
  a warehouse FK; for metrics that have no warehouse dimension (e.g.
  refund), the filter is silently ignored.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ---------------- Filters ----------------


class DashboardRangeFilter(StrictModel):
    """Date-range + warehouse filter shared by most metric endpoints."""

    starts_on: date | None = None
    ends_on: date | None = None
    warehouse_code: str | None = Field(default=None, max_length=32)


# ---------------- Sales ----------------


class SalesByPaymentMethod(StrictModel):
    payment_method: str
    order_count: int
    revenue: Decimal


class SalesDailyPoint(StrictModel):
    day: date
    order_count: int
    revenue: Decimal


class SalesMetrics(StrictModel):
    starts_on: date
    ends_on: date
    order_count: int
    revenue: Decimal
    avg_order_value: Decimal
    cancelled_count: int
    cancelled_revenue: Decimal  # gross of cancelled orders for visibility
    by_payment_method: list[SalesByPaymentMethod]
    daily: list[SalesDailyPoint]


# ---------------- Stock ----------------


class StockByBucket(StrictModel):
    bucket: str
    units: int


class LowStockVariant(StrictModel):
    variant_id: UUID
    variant_sku: str
    product_name: str
    available_units: int


class StockMetrics(StrictModel):
    as_of: datetime
    warehouse_code: str | None
    by_bucket: list[StockByBucket]
    available_units_total: int
    distinct_variants_in_stock: int
    low_stock_threshold: int
    low_stock_variants: list[LowStockVariant]


# ---------------- Expiry ----------------


class ExpiringBatch(StrictModel):
    batch_id: UUID
    batch_number: str
    variant_id: UUID
    variant_sku: str
    product_name: str
    expiry_date: date
    days_to_expiry: int
    units_in_stock: int  # available + reserved + blocked


class ExpiryMetrics(StrictModel):
    as_of: date
    horizon_days: int
    warehouse_code: str | None
    expired_batches: int
    expiring_within_horizon_batches: int
    units_at_risk: int
    batches: list[ExpiringBatch]


# ---------------- Delivery ----------------


class DeliveryMetrics(StrictModel):
    starts_on: date
    ends_on: date
    assigned: int
    picked_up: int
    delivered: int
    completed: int
    cancelled: int
    failed: int
    in_transit: int  # currently picked_up but not yet delivered
    awaiting_assignment: int  # orders dispatched but with no assignment yet
    completion_rate: Decimal  # completed / (completed + failed + cancelled)
    avg_minutes_assignment_to_completion: Decimal | None


# ---------------- COD ----------------


class RiderCashOnHand(StrictModel):
    rider_id: UUID
    rider_code: str
    rider_name: str
    expected_total: Decimal
    deposited_total: Decimal
    outstanding: Decimal


class CodMetrics(StrictModel):
    starts_on: date
    ends_on: date
    cod_collected_total: Decimal  # from delivery assignments completed in range
    cod_deposited_total: Decimal  # from cod_deposits in range
    cod_outstanding_total: Decimal  # collected - deposited (across all riders)
    discrepancy_count: int  # deposits with status='discrepancy'
    discrepancy_total: Decimal  # sum of |discrepancy| for discrepancy deposits
    riders: list[RiderCashOnHand]


# ---------------- Refund ----------------


class RefundMetrics(StrictModel):
    starts_on: date
    ends_on: date
    pending_count: int
    pending_amount: Decimal
    paid_count: int
    paid_amount: Decimal
    cancelled_count: int
    refund_rate: Decimal  # refund records / completed orders in range


# ---------------- Overview ----------------


class DashboardOverview(StrictModel):
    """Combined snapshot for the home page — uses the same date range
    across every block.
    """

    starts_on: date
    ends_on: date
    sales: SalesMetrics
    stock: StockMetrics
    expiry: ExpiryMetrics
    delivery: DeliveryMetrics
    cod: CodMetrics
    refund: RefundMetrics
