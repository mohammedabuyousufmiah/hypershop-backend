"""Dashboard admin endpoints.

Every endpoint accepts ``starts_on`` / ``ends_on`` as optional query
params. When omitted the service defaults to "last 30 days" (rolling
window ending today). Range cannot exceed ``MAX_RANGE_DAYS`` (366).

Filters
-------
- ``starts_on`` / ``ends_on`` (date, inclusive). Default: last 30 days.
- ``warehouse_code`` (str). Applies to stock + expiry endpoints only.
- ``low_stock_threshold`` (int) — variants with available ≤ this are
  flagged. Default 10.
- ``horizon_days`` (int) — expiry horizon in days. Default 60.
- ``rider_limit`` / ``leaderboard_limit`` — list result caps.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.dashboard.schemas import (
    CodMetrics,
    DashboardOverview,
    DeliveryMetrics,
    ExpiryMetrics,
    RefundMetrics,
    SalesMetrics,
    StockMetrics,
)
from app.modules.dashboard.service import (
    DEFAULT_EXPIRY_HORIZON_DAYS,
    DEFAULT_EXPIRY_LIMIT,
    DEFAULT_LOW_STOCK_LIMIT,
    DEFAULT_LOW_STOCK_THRESHOLD,
    DEFAULT_RIDER_LIMIT,
    DashboardService,
)

router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])

_READ = "dashboard.read"


# ---------------- Sales ----------------


@router.get(
    "/sales",
    response_model=SalesMetrics,
    summary="Sales: revenue, AOV, daily trend, payment-method split",
    dependencies=[Depends(requires_permission(_READ))],
)
async def sales(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    starts_on: date | None = Query(default=None),
    ends_on: date | None = Query(default=None),
) -> SalesMetrics:
    async with uow.transactional() as session:
        svc = DashboardService(session)
        return SalesMetrics(
            **await svc.sales(starts_on=starts_on, ends_on=ends_on),
        )


# ---------------- Stock ----------------


@router.get(
    "/stock",
    response_model=StockMetrics,
    summary="Stock: per-bucket totals + low-stock list",
    dependencies=[Depends(requires_permission(_READ))],
)
async def stock(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    warehouse_code: str | None = Query(default=None, max_length=32),
    low_stock_threshold: int = Query(
        default=DEFAULT_LOW_STOCK_THRESHOLD, ge=0, le=10_000,
    ),
    low_stock_limit: int = Query(
        default=DEFAULT_LOW_STOCK_LIMIT, ge=1, le=500,
    ),
) -> StockMetrics:
    async with uow.transactional() as session:
        svc = DashboardService(session)
        return StockMetrics(
            **await svc.stock(
                warehouse_code=warehouse_code,
                low_stock_threshold=low_stock_threshold,
                low_stock_limit=low_stock_limit,
            ),
        )


# ---------------- Expiry ----------------


@router.get(
    "/expiry",
    response_model=ExpiryMetrics,
    summary="Batches expired or expiring within ``horizon_days``",
    dependencies=[Depends(requires_permission(_READ))],
)
async def expiry(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    as_of: date | None = Query(default=None),
    horizon_days: int = Query(
        default=DEFAULT_EXPIRY_HORIZON_DAYS, ge=0, le=365,
    ),
    warehouse_code: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=DEFAULT_EXPIRY_LIMIT, ge=1, le=500),
) -> ExpiryMetrics:
    async with uow.transactional() as session:
        svc = DashboardService(session)
        return ExpiryMetrics(
            **await svc.expiry(
                as_of=as_of,
                horizon_days=horizon_days,
                warehouse_code=warehouse_code,
                limit=limit,
            ),
        )


# ---------------- Delivery ----------------


@router.get(
    "/delivery",
    response_model=DeliveryMetrics,
    summary="Delivery throughput + completion rate + avg minutes",
    dependencies=[Depends(requires_permission(_READ))],
)
async def delivery(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    starts_on: date | None = Query(default=None),
    ends_on: date | None = Query(default=None),
) -> DeliveryMetrics:
    async with uow.transactional() as session:
        svc = DashboardService(session)
        return DeliveryMetrics(
            **await svc.delivery(starts_on=starts_on, ends_on=ends_on),
        )


# ---------------- COD ----------------


@router.get(
    "/cod",
    response_model=CodMetrics,
    summary="COD collected, deposited, outstanding, per-rider breakdown",
    dependencies=[Depends(requires_permission(_READ))],
)
async def cod(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    starts_on: date | None = Query(default=None),
    ends_on: date | None = Query(default=None),
    rider_limit: int = Query(default=DEFAULT_RIDER_LIMIT, ge=1, le=200),
) -> CodMetrics:
    async with uow.transactional() as session:
        svc = DashboardService(session)
        return CodMetrics(
            **await svc.cod(
                starts_on=starts_on,
                ends_on=ends_on,
                rider_limit=rider_limit,
            ),
        )


# ---------------- Refund ----------------


@router.get(
    "/refund",
    response_model=RefundMetrics,
    summary="Refund pipeline + refund rate against completed orders",
    dependencies=[Depends(requires_permission(_READ))],
)
async def refund(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    starts_on: date | None = Query(default=None),
    ends_on: date | None = Query(default=None),
) -> RefundMetrics:
    async with uow.transactional() as session:
        svc = DashboardService(session)
        return RefundMetrics(
            **await svc.refund(starts_on=starts_on, ends_on=ends_on),
        )


# ---------------- Overview ----------------


@router.get(
    "/overview",
    response_model=DashboardOverview,
    summary="Combined home-page snapshot — every metric block in one call",
    dependencies=[Depends(requires_permission(_READ))],
)
async def overview(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    starts_on: date | None = Query(default=None),
    ends_on: date | None = Query(default=None),
    warehouse_code: str | None = Query(default=None, max_length=32),
) -> DashboardOverview:
    async with uow.transactional() as session:
        svc = DashboardService(session)
        return DashboardOverview(
            **await svc.overview(
                starts_on=starts_on,
                ends_on=ends_on,
                warehouse_code=warehouse_code,
            ),
        )


# ---------------- Command Center (graphical super-admin home) ----------------


def _f(v: object) -> float:
    """Decimal/None → float, JSON-safe."""
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)  # type: ignore[arg-type]


@router.get(
    "/command-center",
    summary="Composite snapshot for the graphical super-admin dashboard.",
    dependencies=[Depends(requires_permission(_READ))],
)
async def command_center(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, object]:
    """One call → KPIs + 14-day trend + status donut + top sellers +
    sales-by-city + platform health. Reads real ``orders`` data; every
    block is independently guarded so a missing table/column degrades to
    a sane default instead of 500-ing the whole dashboard.
    """
    # ---- inventory KPIs (use their own short-lived txn so they are not
    # affected by the dashboard's main transaction). ----
    async def _inv_scalar(sql: str, default: object = 0) -> object:
        try:
            async with uow.transactional() as inv_session:
                v = (await inv_session.execute(text(sql))).scalar_one_or_none()
                return v if v is not None else default
        except Exception:
            return default

    _inv_asset = _f(await _inv_scalar(
        "SELECT COALESCE(SUM(sb.quantity * COALESCE(v.cost, v.price * 0.7, 0)), 0) "
        "FROM stock_balances sb "
        "JOIN batches b ON b.id = sb.batch_id "
        "JOIN product_variants v ON v.id = b.variant_id "
        "WHERE sb.quantity > 0"))
    _inv_units = int(await _inv_scalar(
        "SELECT COALESCE(SUM(quantity), 0) FROM stock_balances WHERE quantity > 0") or 0)
    _inv_low = int(await _inv_scalar(
        "SELECT count(*) FROM inventory_stocks "
        "WHERE available_qty <= low_stock_threshold AND low_stock_threshold > 0") or 0)
    _inv_dl = int(await _inv_scalar(
        "SELECT COALESCE(SUM(damaged_qty + lost_qty), 0) FROM inventory_stocks") or 0)

    async with uow.transactional() as session:

        async def scalar(sql: str, default: object = 0, **p: object) -> object:
            try:
                return (await session.execute(text(sql), p)).scalar_one_or_none() or default
            except Exception:
                return default

        async def rows(sql: str, **p: object) -> list:
            try:
                return list((await session.execute(text(sql), p)).all())
            except Exception:
                return []

        # ---- KPIs (today vs yesterday) ----
        gmv_today = _f(await scalar(
            "SELECT COALESCE(SUM(grand_total),0) FROM orders WHERE created_at::date = current_date"))
        gmv_yest = _f(await scalar(
            "SELECT COALESCE(SUM(grand_total),0) FROM orders WHERE created_at::date = current_date - 1"))
        orders_today = int(await scalar(
            "SELECT count(*) FROM orders WHERE created_at::date = current_date"))
        orders_yest = int(await scalar(
            "SELECT count(*) FROM orders WHERE created_at::date = current_date - 1"))
        fraud_open = int(await scalar(
            "SELECT count(*) FROM fraud_flags WHERE status = 'open'", default=0))
        sla_breaches = int(await scalar(
            "SELECT count(*) FROM dispatch_tasks WHERE sla_breached = true "
            "AND created_at::date = current_date", default=0))

        def pct(now: float, prev: float) -> float:
            if prev <= 0:
                return 0.0
            return round((now - prev) / prev * 100, 1)

        # Inventory KPIs were precomputed above in their own transaction.
        inventory_asset_value = _inv_asset
        inventory_units = _inv_units
        inventory_low_stock = _inv_low
        inventory_damaged_lost_units = _inv_dl

        kpis = {
            "gmv_today": gmv_today, "gmv_delta_pct": pct(gmv_today, gmv_yest),
            "orders": orders_today, "orders_delta_pct": pct(float(orders_today), float(orders_yest)),
            "fraud_flags": fraud_open, "fraud_need_review": min(fraud_open, 3),
            "sla_breaches": sla_breaches,
            "inventory_asset_value": inventory_asset_value,
            "inventory_units": inventory_units,
            "inventory_low_stock": inventory_low_stock,
            "inventory_damaged_lost_units": inventory_damaged_lost_units,
        }

        # ---- 14-day trend (fill gaps) ----
        trend_raw = await rows(
            "SELECT created_at::date AS d, count(*) AS o, COALESCE(SUM(grand_total),0) AS g "
            "FROM orders WHERE created_at >= current_date - 13 GROUP BY d ORDER BY d")
        by_day = {str(r[0]): (int(r[1]), _f(r[2])) for r in trend_raw}
        trend = []
        for i in range(13, -1, -1):
            d = date.today() - timedelta(days=i)
            o, g = by_day.get(str(d), (0, 0.0))
            trend.append({"day": d.strftime("%m-%d"), "orders": o, "gmv": round(g)})

        # ---- status breakdown ----
        status_breakdown = [
            {"status": str(r[0]), "count": int(r[1])}
            for r in await rows(
                "SELECT status, count(*) FROM orders GROUP BY status ORDER BY count(*) DESC")
        ]

        # ---- sales by city (delivery_address JSONB) ----
        divisions = [
            {"name": str(r[0] or "Unknown"), "sales": round(_f(r[1]))}
            for r in await rows(
                "SELECT delivery_address->>'city' AS city, COALESCE(SUM(grand_total),0) AS g "
                "FROM orders GROUP BY city ORDER BY g DESC NULLS LAST LIMIT 8")
        ]

        # ---- top sellers (order_items → products.seller_id → sellers) ----
        top_sellers = [
            {"name": str(r[0] or "—"), "gmv": round(_f(r[1]))}
            for r in await rows(
                "SELECT COALESCE(s.shop_name, s.legal_name, 'Seller') AS name, "
                "       COALESCE(SUM(oi.line_total),0) AS g "
                "FROM order_items oi "
                "JOIN products p ON p.id = oi.product_id "
                "JOIN sellers s ON s.id = p.seller_id "
                "GROUP BY name ORDER BY g DESC LIMIT 6")
        ]

        # ---- platform health (payment-success real; rest best-effort) ----
        total_orders = int(await scalar("SELECT count(*) FROM orders", default=0))
        failed_orders = int(await scalar(
            "SELECT count(*) FROM orders WHERE status IN ('failed','cancelled')", default=0))
        pay_success = round((total_orders - failed_orders) / total_orders * 100, 1) if total_orders else 0.0
        health = [
            {"label": "Payment", "value": pay_success, "unit": "%"},
            {"label": "Delivery", "value": _f(await scalar(
                "SELECT round(100.0*count(*) FILTER (WHERE status='completed')/NULLIF(count(*),0),1) "
                "FROM orders", default=0)), "unit": "%"},
            {"label": "Cancel", "value": round(failed_orders / total_orders * 100, 1) if total_orders else 0.0, "unit": "%"},
        ]

        # ---- approval waiting queues (all types) ----
        # Each count runs in its own SAVEPOINT so a missing table/column
        # rolls back only that probe — the outer txn (and every other
        # queue) survives. Counts are 0 when the source isn't present.
        async def _count(sql: str) -> int:
            try:
                async with session.begin_nested():
                    return int((await session.execute(text(sql))).scalar_one())
            except Exception:
                return 0

        _APPROVAL_QUEUES = [
            ("Seller onboarding (KYC)", "/admin/sellers?status=SUBMITTED", "sellers.approve",
             "SELECT count(*) FROM sellers WHERE lower(status) IN ('submitted','pending','pending_review','under_review')"),
            ("Rider KYC", "/admin/rider-kyc", "rider_kyc.review",
             "SELECT count(*) FROM rider_kyc_submissions WHERE lower(status) IN ('pending','submitted','under_review')"),
            ("Returns (RMA)", "/admin/returns?status=REQUESTED", "returns.process",
             "SELECT count(*) FROM returns WHERE lower(status) IN ('requested','pending')"),
            ("Disputes mediation", "/admin/disputes", "disputes.mediate",
             "SELECT count(*) FROM disputes WHERE lower(status) IN ('open','mediation','escalated','pending')"),
            ("Fraud review", "/admin/fraud?decision=CHALLENGE", "reviews.admin",
             "SELECT count(*) FROM fraud_flags WHERE lower(status) IN ('open','review')"),
            ("Seller payouts", "/admin/payouts?status=PENDING_APPROVAL", "finance.settle",
             "SELECT count(*) FROM payout_batches WHERE lower(status) IN ('pending_approval','pending')"),
            ("Supplier payments", "/admin/supplier-payments", "finance.settle",
             "SELECT count(*) FROM supplier_bills WHERE lower(status) IN ('pending','approval','unpaid')"),
            ("Ad / brand review", "/admin/ads/brand-review", "ads.view",
             "SELECT count(*) FROM hypershop_ad_brand_creatives WHERE lower(status) IN ('pending_review','pending')"),
            ("Bulk upload review", "/admin/bulk-upload", "bulk_upload.manage",
             "SELECT count(*) FROM bulk_upload_jobs WHERE lower(status) IN ('pending','review','needs_review')"),
        ]
        approvals = []
        for label, route, perm, sql in _APPROVAL_QUEUES:
            approvals.append({"label": label, "route": route, "perm": perm, "count": await _count(sql)})
        approvals_total = sum(a["count"] for a in approvals)

        return {
            "kpis": kpis,
            "trend": trend,
            "status_breakdown": status_breakdown,
            "top_sellers": top_sellers,
            "divisions": divisions,
            "health": health,
            "approvals": approvals,
            "approvals_total": approvals_total,
            "generated_at": None,
        }
