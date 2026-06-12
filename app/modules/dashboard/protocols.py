"""Structural-typing contracts for dashboard data sources.

Implementing these `typing.Protocol` interfaces on an external Order /
Inventory / Stock model lets the dashboard widgets consume that model
directly — no inheritance, no adapter class, no DTO copy. Just match the
field names + types.

Why protocols (not ABCs):
    * No coupling — your model doesn't import anything from `app.modules.dashboard`.
    * Multiple inheritance avoidance — your ORM model can keep its own base.
    * Plays well with SQLAlchemy `Mapped[...]` columns at runtime
      (their declared type is the Python type, not the column descriptor).
    * `@runtime_checkable` lets the dashboard resolver do a defensive
      `isinstance(row, IDashboardOrder)` check before reading attributes.

Usage in a dashboard resolver::

    from app.modules.dashboard.protocols import IDashboardOrder

    async def _resolve_kpi(session, principal) -> dict:
        rows = (await session.execute(select(Order))).scalars().all()
        # Type narrowing — drop any row that doesn't satisfy the contract.
        valid: list[IDashboardOrder] = [r for r in rows if isinstance(r, IDashboardOrder)]
        return {
            "gmv":          sum(o.gmv for o in valid),
            "net_revenue":  sum(o.net_revenue for o in valid),
            "net_profit":   sum(o.net_profit for o in valid),
        }

Important — runtime caveats of `@runtime_checkable` Protocol:
    * `isinstance()` only verifies attribute *presence*, NOT types.
      An int-typed field that's actually a `Decimal` will pass the
      check; widgets must defensively coerce with `float(...)` where
      they need numeric arithmetic.
    * Mark new fields on the protocol as ``... | None`` if older models
      may not have them — otherwise runtime instances will silently
      fail the structural check.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class IDashboardOrder(Protocol):
    """Implement this protocol on your existing Order model to map directly."""
    id: int
    status: str
    gmv: float
    net_revenue: float
    net_profit: float
    customer_satisfaction: float
    created_at: datetime


@runtime_checkable
class IDashboardInventory(Protocol):
    """Implement this protocol on your existing Inventory/Stock model."""
    id: int
    stock_count: int
    safety_threshold: int


__all__ = [
    "IDashboardOrder",
    "IDashboardInventory",
]
