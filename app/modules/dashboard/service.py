"""Dashboard service.

Thin orchestration over :class:`DashboardRepository`. Owns:

- Default date-range resolution (``last 30 days`` when omitted).
- Hard limit on range width (``MAX_RANGE_DAYS``) to avoid runaway scans.
- Computed fields the repo doesn't bother with (e.g. ``avg_order_value``).
- Composing the multi-block ``DashboardOverview`` in a single
  transaction so all metric blocks reflect the same as-of state.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.time import utc_now
from app.modules.dashboard.repository import DashboardRepository

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 366
DEFAULT_LOW_STOCK_THRESHOLD = 10
DEFAULT_LOW_STOCK_LIMIT = 50
DEFAULT_EXPIRY_HORIZON_DAYS = 60
DEFAULT_EXPIRY_LIMIT = 100
DEFAULT_RIDER_LIMIT = 25
DEFAULT_LEADERBOARD_LIMIT = 20

_TWO_PLACES = Decimal("0.01")


def resolve_range(
    starts_on: date | None, ends_on: date | None,
) -> tuple[date, date]:
    """Defaults: ends_on = today, starts_on = ends_on - 30d. Caps at
    ``MAX_RANGE_DAYS`` to keep aggregations bounded.
    """
    today = utc_now().date()
    end = ends_on or today
    start = starts_on or (end - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    if start > end:
        raise ValidationError("starts_on must be ≤ ends_on.")
    span = (end - start).days
    if span > MAX_RANGE_DAYS:
        raise ValidationError(
            f"Date range cannot exceed {MAX_RANGE_DAYS} days.",
            details={"requested_days": span, "max_days": MAX_RANGE_DAYS},
        )
    return start, end


def _q(amount: Decimal | int | float) -> Decimal:
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DashboardRepository(session)

    # ------------------------------------------------------------------
    # Sales
    # ------------------------------------------------------------------

    async def sales(
        self, *, starts_on: date | None, ends_on: date | None,
    ) -> dict[str, Any]:
        start, end = resolve_range(starts_on, ends_on)
        data = await self.repo.sales_summary(starts_on=start, ends_on=end)
        order_count = data["order_count"]
        revenue = data["revenue"]
        aov = (
            _q(Decimal(revenue) / Decimal(order_count))
            if order_count > 0 else Decimal("0.00")
        )
        return {
            "starts_on": start,
            "ends_on": end,
            "order_count": order_count,
            "revenue": _q(revenue),
            "avg_order_value": aov,
            "cancelled_count": data["cancelled_count"],
            "cancelled_revenue": _q(data["cancelled_revenue"]),
            "by_payment_method": [
                {
                    **row,
                    "revenue": _q(row["revenue"]),
                }
                for row in data["by_payment_method"]
            ],
            "daily": [
                {**row, "revenue": _q(row["revenue"])}
                for row in data["daily"]
            ],
        }

    # ------------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------------

    async def stock(
        self,
        *,
        warehouse_code: str | None,
        low_stock_threshold: int,
        low_stock_limit: int,
    ) -> dict[str, Any]:
        if low_stock_threshold < 0:
            raise ValidationError("low_stock_threshold must be ≥ 0.")
        data = await self.repo.stock_summary(
            warehouse_code=warehouse_code,
            low_stock_threshold=low_stock_threshold,
            low_stock_limit=low_stock_limit,
        )
        return {
            "as_of": utc_now(),
            "warehouse_code": warehouse_code,
            "low_stock_threshold": low_stock_threshold,
            **data,
        }

    # ------------------------------------------------------------------
    # Expiry
    # ------------------------------------------------------------------

    async def expiry(
        self,
        *,
        as_of: date | None,
        horizon_days: int,
        warehouse_code: str | None,
        limit: int,
    ) -> dict[str, Any]:
        if horizon_days < 0:
            raise ValidationError("horizon_days must be ≥ 0.")
        as_of = as_of or utc_now().date()
        data = await self.repo.expiry_summary(
            as_of=as_of,
            horizon_days=horizon_days,
            warehouse_code=warehouse_code,
            limit=limit,
        )
        return {
            "as_of": as_of,
            "horizon_days": horizon_days,
            "warehouse_code": warehouse_code,
            **data,
        }

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    async def delivery(
        self, *, starts_on: date | None, ends_on: date | None,
    ) -> dict[str, Any]:
        start, end = resolve_range(starts_on, ends_on)
        data = await self.repo.delivery_summary(starts_on=start, ends_on=end)
        return {"starts_on": start, "ends_on": end, **data}

    # ------------------------------------------------------------------
    # COD
    # ------------------------------------------------------------------

    async def cod(
        self,
        *,
        starts_on: date | None,
        ends_on: date | None,
        rider_limit: int,
    ) -> dict[str, Any]:
        start, end = resolve_range(starts_on, ends_on)
        data = await self.repo.cod_summary(
            starts_on=start, ends_on=end, rider_limit=rider_limit,
        )
        return {
            "starts_on": start,
            "ends_on": end,
            "cod_collected_total": _q(data["cod_collected_total"]),
            "cod_deposited_total": _q(data["cod_deposited_total"]),
            "cod_outstanding_total": _q(data["cod_outstanding_total"]),
            "discrepancy_count": data["discrepancy_count"],
            "discrepancy_total": _q(data["discrepancy_total"]),
            "riders": [
                {
                    **r,
                    "expected_total": _q(r["expected_total"]),
                    "deposited_total": _q(r["deposited_total"]),
                    "outstanding": _q(r["outstanding"]),
                }
                for r in data["riders"]
            ],
        }

    # ------------------------------------------------------------------
    # Refund
    # ------------------------------------------------------------------

    async def refund(
        self, *, starts_on: date | None, ends_on: date | None,
    ) -> dict[str, Any]:
        start, end = resolve_range(starts_on, ends_on)
        data = await self.repo.refund_summary(starts_on=start, ends_on=end)
        return {
            "starts_on": start,
            "ends_on": end,
            "pending_count": data["pending_count"],
            "pending_amount": _q(data["pending_amount"]),
            "paid_count": data["paid_count"],
            "paid_amount": _q(data["paid_amount"]),
            "cancelled_count": data["cancelled_count"],
            "refund_rate": data["refund_rate"],
        }

    # ------------------------------------------------------------------
    # Overview (combined snapshot)
    # ------------------------------------------------------------------

    async def overview(
        self,
        *,
        starts_on: date | None,
        ends_on: date | None,
        warehouse_code: str | None,
    ) -> dict[str, Any]:
        start, end = resolve_range(starts_on, ends_on)
        # All blocks share the same date range so the home-page snapshot is
        # internally consistent.
        sales = await self.sales(starts_on=start, ends_on=end)
        stock = await self.stock(
            warehouse_code=warehouse_code,
            low_stock_threshold=DEFAULT_LOW_STOCK_THRESHOLD,
            low_stock_limit=DEFAULT_LOW_STOCK_LIMIT,
        )
        expiry = await self.expiry(
            as_of=end,
            horizon_days=DEFAULT_EXPIRY_HORIZON_DAYS,
            warehouse_code=warehouse_code,
            limit=DEFAULT_EXPIRY_LIMIT,
        )
        delivery = await self.delivery(starts_on=start, ends_on=end)
        cod = await self.cod(
            starts_on=start, ends_on=end, rider_limit=DEFAULT_RIDER_LIMIT,
        )
        refund = await self.refund(starts_on=start, ends_on=end)
        return {
            "starts_on": start,
            "ends_on": end,
            "sales": sales,
            "stock": stock,
            "expiry": expiry,
            "delivery": delivery,
            "cod": cod,
            "refund": refund,
        }
