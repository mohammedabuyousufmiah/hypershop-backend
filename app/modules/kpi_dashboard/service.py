"""KpiDashboardService — role-scoped, read-only aggregations.

Every method is a SELECT. There is no write path. The principal's
roles map to one of the four tiers via :func:`resolve_tier` and the
service builds the payload additively (super_admin > admin >
supervisor > staff).

Why direct SQL? The dashboard reads from many modules and we don't
want a hard dependency on every module's service layer (and their
serialisers). Raw SELECTs against well-indexed columns are cheap and
keep the surface contained.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.principal import Principal

from .schemas import (
    Alert,
    DeepLink,
    DonutChart,
    DonutSlice,
    HorizontalBar,
    HorizontalBarPoint,
    KpiCard,
    KpiDashboardResponse,
    KpiFilters,
    LineChart,
    LinePoint,
    RoundBar,
)

Tier = Literal["staff", "supervisor", "admin", "super_admin"]

_TIER_ORDER: tuple[Tier, ...] = ("staff", "supervisor", "admin", "super_admin")

# Map auth roles → dashboard tier. Anything not in here gets the lowest
# tier (staff) so we never over-share, even on unexpected role strings.
_ROLE_TO_TIER: dict[str, Tier] = {
    "super_admin": "super_admin",
    "superadmin": "super_admin",
    "admin": "admin",
    "manager": "admin",
    "dispatcher": "supervisor",
    "supervisor": "supervisor",
    "staff": "staff",
    "pharmacist": "staff",
}

DEFAULT_WINDOW_DAYS = 30


def resolve_tier(principal: Principal) -> Tier:
    """Pick the highest tier any of the principal's roles map to."""
    best: Tier = "staff"
    best_rank = 0
    for r in principal.roles:
        t = _ROLE_TO_TIER.get(r.lower())
        if t is None:
            continue
        rank = _TIER_ORDER.index(t)
        if rank > best_rank:
            best, best_rank = t, rank
    return best


def _resolve_window(filters: KpiFilters) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    end = filters.date_to or today
    start = filters.date_from or (end - timedelta(days=DEFAULT_WINDOW_DAYS - 1))
    return start, end


class KpiDashboardService:
    """Composes role-scoped KPI payloads from existing tables.

    Read-only. No write methods. Every public method returns either a
    Pydantic schema instance or a primitive.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ──────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────

    async def build(
        self,
        *,
        principal: Principal,
        filters: KpiFilters,
    ) -> KpiDashboardResponse:
        tier = resolve_tier(principal)
        start, end = _resolve_window(filters)

        cards: list[KpiCard] = []
        round_bars: list[RoundBar] = []
        h_bars: list[HorizontalBar] = []
        donuts: list[DonutChart] = []
        lines: list[LineChart] = []
        alerts: list[Alert] = []
        deep: list[DeepLink] = []

        # Tier-additive: each tier adds its own contributions on top of
        # everything the lower tiers contribute. The order matters for
        # which cards appear first.
        await self._add_staff_block(cards, round_bars, donuts, lines, alerts, deep, start, end, filters)
        if tier in ("supervisor", "admin", "super_admin"):
            await self._add_supervisor_block(cards, round_bars, donuts, alerts, deep, start, end, filters)
        if tier in ("admin", "super_admin"):
            await self._add_admin_block(cards, h_bars, donuts, alerts, deep, start, end, filters)
        if tier == "super_admin":
            await self._add_super_admin_block(cards, alerts, deep)

        return KpiDashboardResponse(
            tier=tier,
            date_from=start,
            date_to=end,
            kpi_cards=cards,
            round_bars=round_bars,
            horizontal_bars=h_bars,
            donut_charts=donuts,
            line_charts=lines,
            alerts=alerts,
            deep_links=deep,
        )

    # ──────────────────────────────────────────────────────────────────
    # Tier blocks
    # ──────────────────────────────────────────────────────────────────

    async def _add_staff_block(
        self,
        cards: list[KpiCard],
        round_bars: list[RoundBar],
        donuts: list[DonutChart],
        lines: list[LineChart],
        alerts: list[Alert],
        deep: list[DeepLink],
        start: date,
        end: date,
        filters: KpiFilters,
    ) -> None:
        """Front-line operator metrics — orders volume, status mix, daily line."""

        agg = await self._orders_aggregate(start, end, filters)

        cards.append(KpiCard(
            code="orders_placed",
            label="Orders placed",
            unit="count",
            value=str(agg["order_count"]),
            raw=Decimal(agg["order_count"]),
        ))
        cards.append(KpiCard(
            code="orders_completed",
            label="Orders completed",
            unit="count",
            value=str(agg["completed_count"]),
            raw=Decimal(agg["completed_count"]),
        ))
        cards.append(KpiCard(
            code="orders_cancelled",
            label="Orders cancelled",
            unit="count",
            value=str(agg["cancelled_count"]),
            raw=Decimal(agg["cancelled_count"]),
        ))

        # Completion rate as a round-bar gauge.
        total_terminal = agg["completed_count"] + agg["cancelled_count"]
        if total_terminal > 0:
            percent = (agg["completed_count"] / total_terminal) * 100.0
        else:
            percent = 0.0
        round_bars.append(RoundBar(
            code="completion_rate",
            label="Completion rate",
            percent=round(percent, 2),
            severity="info" if percent >= 80 else ("warn" if percent >= 50 else "error"),
            caption=f"{agg['completed_count']} of {total_terminal} terminal orders",
        ))

        # Status mix donut.
        donuts.append(DonutChart(
            code="status_mix",
            label="Order status mix",
            unit="count",
            slices=[
                DonutSlice(label=row["status"], value=Decimal(row["count"]))
                for row in agg["status_breakdown"]
            ],
        ))

        # Per-day order count line.
        lines.append(LineChart(
            code="orders_per_day",
            label="Orders per day",
            unit="count",
            points=[
                LinePoint(on=row["on"], value=Decimal(row["count"]))
                for row in agg["per_day"]
            ],
        ))

        if agg["cancelled_count"] > 0 and agg["order_count"] > 0:
            cancel_pct = agg["cancelled_count"] / agg["order_count"] * 100.0
            if cancel_pct > 10:
                alerts.append(Alert(
                    code="high_cancellation_rate",
                    severity="warn",
                    message=f"Cancellation rate at {cancel_pct:.1f}% — review reasons.",
                    action_href="/admin/orders?status=cancelled",
                ))

        deep.append(DeepLink(code="orders", label="Order processing", href="/admin/orders"))
        deep.append(DeepLink(code="packing", label="Pick & pack queue", href="/admin/packing"))
        deep.append(DeepLink(code="returns", label="Returns queue", href="/admin/returns"))

    async def _add_supervisor_block(
        self,
        cards: list[KpiCard],
        round_bars: list[RoundBar],
        donuts: list[DonutChart],
        alerts: list[Alert],
        deep: list[DeepLink],
        start: date,
        end: date,
        filters: KpiFilters,
    ) -> None:
        """Fleet + COD metrics — read from orders.payment_method split."""

        pm = await self._payment_mix(start, end, filters)

        donuts.append(DonutChart(
            code="payment_method_mix",
            label="Payment method mix",
            unit="count",
            slices=[
                DonutSlice(label=row["payment_method"], value=Decimal(row["count"]))
                for row in pm
            ],
        ))

        cod_count = sum(int(r["count"]) for r in pm if r["payment_method"] == "cod")
        cards.append(KpiCard(
            code="cod_orders",
            label="COD orders",
            unit="count",
            value=str(cod_count),
            raw=Decimal(cod_count),
        ))

        deep.append(DeepLink(code="rider_routing", label="Rider routing", href="/admin/rider-routing"))
        deep.append(DeepLink(code="rider_wallet_ops", label="Rider wallet ops", href="/admin/rider-wallet-ops"))
        deep.append(DeepLink(code="settlement_lock", label="Settlement lock", href="/admin/settlement-lock"))

    async def _add_admin_block(
        self,
        cards: list[KpiCard],
        h_bars: list[HorizontalBar],
        donuts: list[DonutChart],
        alerts: list[Alert],
        deep: list[DeepLink],
        start: date,
        end: date,
        filters: KpiFilters,
    ) -> None:
        """Finance + seller + catalog metrics."""

        rev = await self._revenue_aggregate(start, end, filters)
        cards.append(KpiCard(
            code="gmv",
            label="Gross merchandise value",
            unit="BDT",
            value=f"{rev['gmv']:.2f}",
            raw=rev["gmv"],
        ))
        cards.append(KpiCard(
            code="aov",
            label="Average order value",
            unit="BDT",
            value=f"{rev['aov']:.2f}",
            raw=rev["aov"],
        ))

        top_cities = await self._top_cities(start, end, filters)
        h_bars.append(HorizontalBar(
            code="top_cities",
            label="Top cities by order count",
            unit="count",
            points=[
                HorizontalBarPoint(label=row["city"], value=Decimal(row["count"]))
                for row in top_cities
            ],
        ))

        deep.append(DeepLink(code="sellers", label="Sellers", href="/admin/sellers"))
        deep.append(DeepLink(code="finance", label="Finance", href="/admin/finance"))
        deep.append(DeepLink(code="bi", label="Business intelligence", href="/admin/bi"))

    async def _add_super_admin_block(
        self,
        cards: list[KpiCard],
        alerts: list[Alert],
        deep: list[DeepLink],
    ) -> None:
        """Platform-wide signals + break-glass deep links."""

        deep.append(DeepLink(code="admin_lite", label="Admin lite (break-glass)", href="/admin/admin-lite"))
        deep.append(DeepLink(code="security_hardening", label="Security hardening", href="/admin/security-hardening"))
        deep.append(DeepLink(code="automation", label="Automation kill-switches", href="/admin/automation"))

    # ──────────────────────────────────────────────────────────────────
    # SQL helpers (every one is a SELECT)
    # ──────────────────────────────────────────────────────────────────

    async def _orders_aggregate(
        self,
        start: date,
        end: date,
        filters: KpiFilters,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"start": start, "end": end}
        where_extra = ""
        if filters.city_id:
            where_extra += " AND delivery_address->>'city' = :city"
            params["city"] = filters.city_id

        total_sql = text(f"""
            SELECT
                COUNT(*) FILTER (WHERE TRUE) AS order_count,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed_count,
                COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled_count
            FROM orders
            WHERE placed_at::date BETWEEN :start AND :end
            {where_extra}
        """)
        totals = (await self.session.execute(total_sql, params)).mappings().one()

        status_sql = text(f"""
            SELECT status, COUNT(*) AS count
            FROM orders
            WHERE placed_at::date BETWEEN :start AND :end
            {where_extra}
            GROUP BY status
            ORDER BY count DESC
        """)
        breakdown = [
            dict(row) for row in (await self.session.execute(status_sql, params)).mappings()
        ]

        per_day_sql = text(f"""
            SELECT placed_at::date AS on, COUNT(*) AS count
            FROM orders
            WHERE placed_at::date BETWEEN :start AND :end
            {where_extra}
            GROUP BY placed_at::date
            ORDER BY placed_at::date ASC
        """)
        per_day_rows = (await self.session.execute(per_day_sql, params)).mappings().all()

        # Backfill zero-buckets so the line chart spans the whole window.
        per_day_index = {r["on"]: int(r["count"]) for r in per_day_rows}
        per_day: list[dict[str, Any]] = []
        cur = start
        while cur <= end:
            per_day.append({"on": cur, "count": per_day_index.get(cur, 0)})
            cur = cur + timedelta(days=1)

        return {
            "order_count": int(totals["order_count"] or 0),
            "completed_count": int(totals["completed_count"] or 0),
            "cancelled_count": int(totals["cancelled_count"] or 0),
            "status_breakdown": [
                {"status": r["status"], "count": int(r["count"])} for r in breakdown
            ],
            "per_day": per_day,
        }

    async def _payment_mix(
        self,
        start: date,
        end: date,
        filters: KpiFilters,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"start": start, "end": end}
        where_extra = ""
        if filters.city_id:
            where_extra += " AND delivery_address->>'city' = :city"
            params["city"] = filters.city_id
        sql = text(f"""
            SELECT payment_method, COUNT(*) AS count
            FROM orders
            WHERE placed_at::date BETWEEN :start AND :end
            {where_extra}
            GROUP BY payment_method
        """)
        rows = (await self.session.execute(sql, params)).mappings().all()
        return [{"payment_method": r["payment_method"], "count": int(r["count"])} for r in rows]

    async def _revenue_aggregate(
        self,
        start: date,
        end: date,
        filters: KpiFilters,
    ) -> dict[str, Decimal]:
        params: dict[str, Any] = {"start": start, "end": end}
        where_extra = ""
        if filters.city_id:
            where_extra += " AND delivery_address->>'city' = :city"
            params["city"] = filters.city_id
        sql = text(f"""
            SELECT
                COALESCE(SUM(grand_total), 0) AS gmv,
                COUNT(*) AS order_count
            FROM orders
            WHERE placed_at::date BETWEEN :start AND :end
              AND status NOT IN ('cancelled')
            {where_extra}
        """)
        row = (await self.session.execute(sql, params)).mappings().one()
        gmv = Decimal(row["gmv"] or 0)
        order_count = int(row["order_count"] or 0)
        aov = (gmv / order_count) if order_count else Decimal("0")
        return {"gmv": gmv, "aov": aov.quantize(Decimal("0.01"))}

    async def _top_cities(
        self,
        start: date,
        end: date,
        filters: KpiFilters,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"start": start, "end": end}
        sql = text("""
            SELECT delivery_address->>'city' AS city, COUNT(*) AS count
            FROM orders
            WHERE placed_at::date BETWEEN :start AND :end
              AND delivery_address->>'city' IS NOT NULL
            GROUP BY delivery_address->>'city'
            ORDER BY count DESC
            LIMIT 5
        """)
        rows = (await self.session.execute(sql, params)).mappings().all()
        return [{"city": r["city"], "count": int(r["count"])} for r in rows]
