"""Protocol-driven aggregators for dashboard widgets.

Reusable helpers that take any iterable of objects satisfying the
`IDashboardOrder` / `IDashboardInventory` protocols and return the
aggregate dict shape widgets expect. Decouples *how* you fetch the
rows (raw SQL, ORM query, JSON dump, a list of dataclasses in tests)
from *what* you compute over them.

Defensive design:
    * Every entry is gated through `isinstance(row, IDashboard*)` —
      stray rows that don't satisfy the structural contract are
      silently dropped (and counted in `skipped`) instead of raising.
    * Numeric fields are coerced with `float(...)` so `Decimal` /
      `int` / `float` mix without TypeError.
    * Empty input → all-zero result (no division-by-zero).

Usage in a widget resolver::

    from app.modules.dashboard.aggregators import order_kpi_summary
    from app.modules.dashboard.protocols import IDashboardOrder

    async def _resolve_orders_kpi(session, principal) -> dict:
        rows = (await session.execute(select(SomeOrderViewModel))).scalars().all()
        return order_kpi_summary(rows)

Where `SomeOrderViewModel` implements `IDashboardOrder` either as a
plain dataclass, a SQLAlchemy model with `@hybrid_property` adapters,
or a Pydantic projection from a raw SQL row.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.modules.dashboard.protocols import IDashboardInventory, IDashboardOrder


def order_kpi_summary(rows: Iterable[Any]) -> dict[str, float | int]:
    """Aggregate GMV / net revenue / net profit / CSAT over orders.

    Returns::

        {
            "count": int,            # rows that satisfied the protocol
            "skipped": int,          # rows dropped for missing fields
            "gmv": float,            # sum of order GMV
            "net_revenue": float,    # sum of net revenue
            "net_profit": float,     # sum of net profit
            "csat_avg": float,       # mean customer-satisfaction (0..5)
        }
    """
    valid: list[IDashboardOrder] = []
    skipped = 0
    for r in rows:
        if isinstance(r, IDashboardOrder):
            valid.append(r)
        else:
            skipped += 1

    if not valid:
        return {
            "count": 0,
            "skipped": skipped,
            "gmv": 0.0,
            "net_revenue": 0.0,
            "net_profit": 0.0,
            "csat_avg": 0.0,
        }

    gmv = sum(float(o.gmv) for o in valid)
    net_revenue = sum(float(o.net_revenue) for o in valid)
    net_profit = sum(float(o.net_profit) for o in valid)
    csat_avg = sum(float(o.customer_satisfaction) for o in valid) / len(valid)
    return {
        "count": len(valid),
        "skipped": skipped,
        "gmv": gmv,
        "net_revenue": net_revenue,
        "net_profit": net_profit,
        "csat_avg": round(csat_avg, 2),
    }


def low_stock_items(
    rows: Iterable[Any],
    *,
    limit: int | None = None,
) -> list[dict[str, int]]:
    """Return rows where ``stock_count < safety_threshold``, sorted by
    biggest shortfall first.

    Each entry::  {"id": int, "stock_count": int, "safety_threshold": int,
                    "shortfall": int}
    """
    out: list[dict[str, int]] = []
    for r in rows:
        if not isinstance(r, IDashboardInventory):
            continue
        if r.stock_count >= r.safety_threshold:
            continue
        out.append({
            "id": int(r.id),
            "stock_count": int(r.stock_count),
            "safety_threshold": int(r.safety_threshold),
            "shortfall": int(r.safety_threshold - r.stock_count),
        })
    out.sort(key=lambda x: -x["shortfall"])
    if limit is not None:
        out = out[:limit]
    return out


__all__ = ["order_kpi_summary", "low_stock_items"]
