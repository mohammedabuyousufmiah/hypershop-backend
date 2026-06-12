"""Cross-module aggregation queries.

Each method reads from the operational tables of another module and
returns plain dicts shaped for the schemas. The dashboard does NOT cache
— every request is a fresh aggregation. For high-cardinality reports
(e.g. ``leaderboard``), an explicit ``limit`` is enforced.

Convention: every range method takes ``starts_on`` (inclusive),
``ends_on`` (inclusive), and an optional ``warehouse_code``. Where the
underlying table has no warehouse dimension, the warehouse filter is
silently ignored — the dashboard layer is responsible for ignoring or
warning about meaningless filter combinations.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    and_,
    cast,
    distinct,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now


def _start_of_day(d: date) -> datetime:
    return datetime.combine(d, time.min).replace(tzinfo=utc_now().tzinfo)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, time.max).replace(tzinfo=utc_now().tzinfo)


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Sales
    # ------------------------------------------------------------------

    async def sales_summary(
        self, *, starts_on: date, ends_on: date,
    ) -> dict[str, Any]:
        from app.modules.orders.models import Order

        start_dt = _start_of_day(starts_on)
        end_dt = _end_of_day(ends_on)
        in_range = and_(
            Order.placed_at >= start_dt,
            Order.placed_at <= end_dt,
        )

        completed_states = ("payment_confirmed", "stock_reserved",
                            "approved",
                            "packing", "out_for_delivery", "completed")
        # "Revenue" = grand_total of orders that reached payment_confirmed or
        # later (i.e. excludes pending_payment + cancelled). Cancelled
        # orders are reported separately so the dashboard surfaces churn.
        rev_stmt = (
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.grand_total), 0),
            )
            .where(in_range, Order.status.in_(completed_states))
        )
        order_count, revenue = (await self.session.execute(rev_stmt)).one()

        cancel_stmt = (
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.grand_total), 0),
            )
            .where(in_range, Order.status == "cancelled")
        )
        cancelled_count, cancelled_revenue = (
            await self.session.execute(cancel_stmt)
        ).one()

        by_pm_stmt = (
            select(
                Order.payment_method,
                func.count(Order.id),
                func.coalesce(func.sum(Order.grand_total), 0),
            )
            .where(in_range, Order.status.in_(completed_states))
            .group_by(Order.payment_method)
        )
        by_pm = [
            {
                "payment_method": pm,
                "order_count": int(c or 0),
                "revenue": Decimal(r),
            }
            for pm, c, r in (await self.session.execute(by_pm_stmt)).all()
        ]

        daily_stmt = (
            select(
                cast(Order.placed_at, Date).label("day"),
                func.count(Order.id),
                func.coalesce(func.sum(Order.grand_total), 0),
            )
            .where(in_range, Order.status.in_(completed_states))
            .group_by(cast(Order.placed_at, Date))
            .order_by(cast(Order.placed_at, Date))
        )
        daily = [
            {
                "day": d,
                "order_count": int(c or 0),
                "revenue": Decimal(r),
            }
            for d, c, r in (await self.session.execute(daily_stmt)).all()
        ]

        return {
            "order_count": int(order_count or 0),
            "revenue": Decimal(revenue),
            "cancelled_count": int(cancelled_count or 0),
            "cancelled_revenue": Decimal(cancelled_revenue),
            "by_payment_method": by_pm,
            "daily": daily,
        }

    # ------------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------------

    async def stock_summary(
        self,
        *,
        warehouse_code: str | None,
        low_stock_threshold: int,
        low_stock_limit: int,
    ) -> dict[str, Any]:
        from app.modules.catalog.models import Product, ProductVariant
        from app.modules.inventory.models import StockBalance, Warehouse

        warehouse_filter = []
        if warehouse_code is not None:
            wh_id = (
                await self.session.execute(
                    select(Warehouse.id).where(Warehouse.code == warehouse_code),
                )
            ).scalar_one_or_none()
            if wh_id is None:
                # Unknown warehouse → empty result is more useful than a 404
                # on a metric endpoint. The UI can warn separately.
                return {
                    "by_bucket": [],
                    "available_units_total": 0,
                    "distinct_variants_in_stock": 0,
                    "low_stock_variants": [],
                }
            warehouse_filter.append(StockBalance.warehouse_id == wh_id)

        bucket_stmt = (
            select(
                StockBalance.bucket,
                func.coalesce(func.sum(StockBalance.quantity), 0),
            )
            .where(*warehouse_filter)
            .group_by(StockBalance.bucket)
        )
        by_bucket = [
            {"bucket": b, "units": int(q or 0)}
            for b, q in (await self.session.execute(bucket_stmt)).all()
        ]
        available_total = sum(
            (row["units"] for row in by_bucket if row["bucket"] == "available"),
            0,
        )

        # Variants currently in stock (any bucket, qty > 0).
        distinct_stmt = (
            select(func.count(distinct(StockBalance.variant_id)))
            .where(*warehouse_filter, StockBalance.quantity > 0)
        )
        distinct_count = int(
            (await self.session.execute(distinct_stmt)).scalar_one() or 0,
        )

        # Low-stock list: variants whose AVAILABLE total ≤ threshold AND > 0
        # (zero-stock items are usually filtered separately on the UI).
        avail_subq = (
            select(
                StockBalance.variant_id.label("variant_id"),
                func.coalesce(func.sum(StockBalance.quantity), 0).label("avail"),
            )
            .where(
                *warehouse_filter,
                StockBalance.bucket == "available",
            )
            .group_by(StockBalance.variant_id)
            .subquery()
        )
        low_stmt = (
            select(
                avail_subq.c.variant_id,
                avail_subq.c.avail,
                ProductVariant.sku,
                Product.name,
            )
            .join(ProductVariant, ProductVariant.id == avail_subq.c.variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(
                avail_subq.c.avail > 0,
                avail_subq.c.avail <= low_stock_threshold,
            )
            .order_by(avail_subq.c.avail.asc())
            .limit(low_stock_limit)
        )
        low_rows = (await self.session.execute(low_stmt)).all()
        low_stock = [
            {
                "variant_id": vid,
                "variant_sku": sku,
                "product_name": pname,
                "available_units": int(av),
            }
            for vid, av, sku, pname in low_rows
        ]
        return {
            "by_bucket": by_bucket,
            "available_units_total": int(available_total),
            "distinct_variants_in_stock": distinct_count,
            "low_stock_variants": low_stock,
        }

    # ------------------------------------------------------------------
    # Expiry
    # ------------------------------------------------------------------

    async def expiry_summary(
        self,
        *,
        as_of: date,
        horizon_days: int,
        warehouse_code: str | None,
        limit: int,
    ) -> dict[str, Any]:
        from app.modules.catalog.models import Product, ProductVariant
        from app.modules.inventory.models import Batch, StockBalance, Warehouse

        warehouse_filter = []
        if warehouse_code is not None:
            wh_id = (
                await self.session.execute(
                    select(Warehouse.id).where(Warehouse.code == warehouse_code),
                )
            ).scalar_one_or_none()
            if wh_id is None:
                return {
                    "expired_batches": 0,
                    "expiring_within_horizon_batches": 0,
                    "units_at_risk": 0,
                    "batches": [],
                }
            warehouse_filter.append(StockBalance.warehouse_id == wh_id)

        # Per-batch units in stock (sum across non-expired buckets).
        units_subq = (
            select(
                StockBalance.batch_id.label("batch_id"),
                func.coalesce(func.sum(StockBalance.quantity), 0).label("units"),
            )
            .where(
                *warehouse_filter,
                StockBalance.bucket.in_(("available", "reserved", "blocked")),
            )
            .group_by(StockBalance.batch_id)
            .subquery()
        )

        horizon = as_of + timedelta(days=horizon_days)

        # Counts.
        expired_count_stmt = (
            select(func.count())
            .select_from(Batch)
            .join(units_subq, units_subq.c.batch_id == Batch.id)
            .where(Batch.expiry_date < as_of, units_subq.c.units > 0)
        )
        expiring_count_stmt = (
            select(func.count())
            .select_from(Batch)
            .join(units_subq, units_subq.c.batch_id == Batch.id)
            .where(
                Batch.expiry_date >= as_of,
                Batch.expiry_date <= horizon,
                units_subq.c.units > 0,
            )
        )
        units_at_risk_stmt = (
            select(func.coalesce(func.sum(units_subq.c.units), 0))
            .select_from(Batch)
            .join(units_subq, units_subq.c.batch_id == Batch.id)
            .where(Batch.expiry_date <= horizon, units_subq.c.units > 0)
        )

        expired_count = int(
            (await self.session.execute(expired_count_stmt)).scalar_one() or 0,
        )
        expiring_count = int(
            (await self.session.execute(expiring_count_stmt)).scalar_one() or 0,
        )
        units_at_risk = int(
            (await self.session.execute(units_at_risk_stmt)).scalar_one() or 0,
        )

        # Listing — batches expired or expiring within horizon, with stock,
        # ordered by soonest expiry first.
        list_stmt = (
            select(
                Batch.id,
                Batch.batch_number,
                Batch.variant_id,
                Batch.expiry_date,
                units_subq.c.units,
                ProductVariant.sku,
                Product.name,
            )
            .select_from(Batch)
            .join(units_subq, units_subq.c.batch_id == Batch.id)
            .join(ProductVariant, ProductVariant.id == Batch.variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(Batch.expiry_date <= horizon, units_subq.c.units > 0)
            .order_by(Batch.expiry_date.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(list_stmt)).all()
        batches = [
            {
                "batch_id": bid,
                "batch_number": bnum,
                "variant_id": vid,
                "variant_sku": sku,
                "product_name": pname,
                "expiry_date": exp,
                "days_to_expiry": (exp - as_of).days,
                "units_in_stock": int(units or 0),
            }
            for bid, bnum, vid, exp, units, sku, pname in rows
        ]
        return {
            "expired_batches": expired_count,
            "expiring_within_horizon_batches": expiring_count,
            "units_at_risk": units_at_risk,
            "batches": batches,
        }

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    async def delivery_summary(
        self, *, starts_on: date, ends_on: date,
    ) -> dict[str, Any]:
        from app.modules.deliveries.models import DeliveryAssignment

        start_dt = _start_of_day(starts_on)
        end_dt = _end_of_day(ends_on)
        in_range = and_(
            DeliveryAssignment.assigned_at >= start_dt,
            DeliveryAssignment.assigned_at <= end_dt,
        )

        # Per-status counts of assignments created in range.
        status_stmt = (
            select(DeliveryAssignment.status, func.count(DeliveryAssignment.id))
            .where(in_range)
            .group_by(DeliveryAssignment.status)
        )
        counts: dict[str, int] = {
            s: int(c or 0)
            for s, c in (await self.session.execute(status_stmt)).all()
        }

        # Currently in-transit (independent of range — operational signal).
        in_transit_stmt = (
            select(func.count(DeliveryAssignment.id))
            .where(DeliveryAssignment.status == "picked_up")
        )
        in_transit = int(
            (await self.session.execute(in_transit_stmt)).scalar_one() or 0,
        )

        # Awaiting assignment: orders dispatched (out_for_delivery) without
        # any assignment row.
        from sqlalchemy import exists, not_

        from app.modules.orders.models import Order

        awaiting_stmt = (
            select(func.count(Order.id))
            .where(
                Order.status == "out_for_delivery",
                not_(
                    exists().where(DeliveryAssignment.order_id == Order.id),
                ),
            )
        )
        awaiting = int(
            (await self.session.execute(awaiting_stmt)).scalar_one() or 0,
        )

        completed = counts.get("completed", 0)
        cancelled = counts.get("cancelled", 0)
        failed = counts.get("failed", 0)
        terminal = completed + cancelled + failed
        completion_rate = (
            (Decimal(completed) / Decimal(terminal)).quantize(Decimal("0.0001"))
            if terminal > 0 else Decimal("0")
        )

        # Average minutes from assignment to completion.
        avg_stmt = (
            select(
                func.avg(
                    func.extract(
                        "epoch",
                        DeliveryAssignment.completed_at
                        - DeliveryAssignment.assigned_at,
                    )
                    / 60.0,
                ),
            )
            .where(
                in_range,
                DeliveryAssignment.completed_at.isnot(None),
            )
        )
        avg_minutes_raw = (await self.session.execute(avg_stmt)).scalar_one()
        avg_minutes = (
            Decimal(str(round(float(avg_minutes_raw), 1)))
            if avg_minutes_raw is not None else None
        )

        return {
            "assigned": counts.get("assigned", 0),
            "picked_up": counts.get("picked_up", 0),
            "delivered": counts.get("delivered", 0),
            "completed": completed,
            "cancelled": cancelled,
            "failed": failed,
            "in_transit": in_transit,
            "awaiting_assignment": awaiting,
            "completion_rate": completion_rate,
            "avg_minutes_assignment_to_completion": avg_minutes,
        }

    # ------------------------------------------------------------------
    # COD
    # ------------------------------------------------------------------

    async def cod_summary(
        self, *, starts_on: date, ends_on: date, rider_limit: int,
    ) -> dict[str, Any]:
        from app.modules.deliveries.models import DeliveryAssignment, Rider
        from app.modules.finance.models import CodDeposit

        start_dt = _start_of_day(starts_on)
        end_dt = _end_of_day(ends_on)

        # COD collected: completed COD assignments in range with cod_collected.
        collected_stmt = (
            select(
                func.coalesce(func.sum(DeliveryAssignment.cod_collected), 0),
            )
            .where(
                DeliveryAssignment.payment_method == "cod",
                DeliveryAssignment.cod_collected.isnot(None),
                DeliveryAssignment.completed_at >= start_dt,
                DeliveryAssignment.completed_at <= end_dt,
            )
        )
        cod_collected = Decimal(
            (await self.session.execute(collected_stmt)).scalar_one() or 0,
        )

        # Deposits in range.
        dep_stmt = (
            select(
                func.coalesce(func.sum(CodDeposit.deposited_amount), 0),
                func.count(CodDeposit.id).filter(
                    CodDeposit.status == "discrepancy",
                ),
                func.coalesce(
                    func.sum(
                        func.abs(CodDeposit.discrepancy),
                    ).filter(CodDeposit.status == "discrepancy"),
                    0,
                ),
            )
            .where(
                CodDeposit.deposit_date >= starts_on,
                CodDeposit.deposit_date <= ends_on,
            )
        )
        cod_deposited, discrep_count, discrep_total = (
            await self.session.execute(dep_stmt)
        ).one()

        # Per-rider outstanding (lifetime, not range — actionable signal).
        # Expected = sum of cod_collected on reconciled/resolved COD deliveries.
        rider_expected_subq = (
            select(
                DeliveryAssignment.rider_id.label("rider_id"),
                func.coalesce(
                    func.sum(DeliveryAssignment.cod_collected), 0,
                ).label("expected"),
            )
            .where(
                DeliveryAssignment.payment_method == "cod",
                DeliveryAssignment.cod_collected.isnot(None),
                DeliveryAssignment.cod_status.in_(("reconciled", "resolved")),
            )
            .group_by(DeliveryAssignment.rider_id)
            .subquery()
        )
        rider_deposited_subq = (
            select(
                CodDeposit.rider_id.label("rider_id"),
                func.coalesce(
                    func.sum(CodDeposit.deposited_amount), 0,
                ).label("deposited"),
            )
            .group_by(CodDeposit.rider_id)
            .subquery()
        )
        rider_stmt = (
            select(
                Rider.id, Rider.code, Rider.name,
                func.coalesce(rider_expected_subq.c.expected, 0),
                func.coalesce(rider_deposited_subq.c.deposited, 0),
            )
            .select_from(Rider)
            .outerjoin(
                rider_expected_subq,
                rider_expected_subq.c.rider_id == Rider.id,
            )
            .outerjoin(
                rider_deposited_subq,
                rider_deposited_subq.c.rider_id == Rider.id,
            )
            .where(
                func.coalesce(rider_expected_subq.c.expected, 0)
                != func.coalesce(rider_deposited_subq.c.deposited, 0),
            )
            .order_by(
                (
                    func.coalesce(rider_expected_subq.c.expected, 0)
                    - func.coalesce(rider_deposited_subq.c.deposited, 0)
                ).desc(),
            )
            .limit(rider_limit)
        )
        rider_rows = (await self.session.execute(rider_stmt)).all()
        riders = [
            {
                "rider_id": rid,
                "rider_code": rcode,
                "rider_name": rname,
                "expected_total": Decimal(exp),
                "deposited_total": Decimal(dep),
                "outstanding": Decimal(exp) - Decimal(dep),
            }
            for rid, rcode, rname, exp, dep in rider_rows
        ]

        # Total outstanding across all riders (not limited).
        total_expected_stmt = select(
            func.coalesce(func.sum(rider_expected_subq.c.expected), 0),
        )
        total_deposited_stmt = select(
            func.coalesce(func.sum(CodDeposit.deposited_amount), 0),
        )
        total_expected = Decimal(
            (await self.session.execute(total_expected_stmt)).scalar_one() or 0,
        )
        total_deposited_lifetime = Decimal(
            (await self.session.execute(total_deposited_stmt)).scalar_one() or 0,
        )
        total_outstanding = total_expected - total_deposited_lifetime

        return {
            "cod_collected_total": cod_collected,
            "cod_deposited_total": Decimal(cod_deposited),
            "cod_outstanding_total": total_outstanding,
            "discrepancy_count": int(discrep_count or 0),
            "discrepancy_total": Decimal(discrep_total),
            "riders": riders,
        }

    # ------------------------------------------------------------------
    # Refund
    # ------------------------------------------------------------------

    async def refund_summary(
        self, *, starts_on: date, ends_on: date,
    ) -> dict[str, Any]:
        from app.modules.finance.models import RefundRecord
        from app.modules.orders.models import Order

        start_dt = _start_of_day(starts_on)
        end_dt = _end_of_day(ends_on)

        in_range = and_(
            RefundRecord.created_at >= start_dt,
            RefundRecord.created_at <= end_dt,
        )

        breakdown_stmt = (
            select(
                RefundRecord.status,
                func.count(RefundRecord.id),
                func.coalesce(func.sum(RefundRecord.accrued_amount), 0),
            )
            .where(in_range)
            .group_by(RefundRecord.status)
        )
        breakdown: dict[str, tuple[int, Decimal]] = {
            s: (int(c or 0), Decimal(a))
            for s, c, a in (await self.session.execute(breakdown_stmt)).all()
        }

        completed_orders_stmt = (
            select(func.count(Order.id))
            .where(
                Order.status == "completed",
                Order.completed_at >= start_dt,
                Order.completed_at <= end_dt,
            )
        )
        completed_orders = int(
            (await self.session.execute(completed_orders_stmt)).scalar_one() or 0,
        )
        total_refunds = sum(c for c, _ in breakdown.values())
        rate = (
            (Decimal(total_refunds) / Decimal(completed_orders)).quantize(
                Decimal("0.0001"),
            )
            if completed_orders > 0 else Decimal("0")
        )

        pending_count, pending_amount = breakdown.get(
            "pending", (0, Decimal("0")),
        )
        paid_count, paid_amount = breakdown.get("paid", (0, Decimal("0")))
        cancelled_count, _ = breakdown.get("cancelled", (0, Decimal("0")))

        return {
            "pending_count": pending_count,
            "pending_amount": pending_amount,
            "paid_count": paid_count,
            "paid_amount": paid_amount,
            "cancelled_count": cancelled_count,
            "refund_rate": rate,
        }

