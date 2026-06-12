"""Pydantic schemas for the read-only KPI dashboard.

The response is intentionally chart-agnostic: each renderable section is
a list of typed primitives that any chart library (Recharts / Chart.js /
ECharts) can consume directly. Adding a new card never requires a wire
change — append to the relevant section.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.core.validation import StrictModel

# ────────────────────────────────────────────────────────────────────────
# Input — filters accepted by every dashboard tier
# ────────────────────────────────────────────────────────────────────────


class KpiFilters(StrictModel):
    """Query filters accepted by ``GET /kpi-dashboard``.

    Every filter is optional. When a filter targets a column that does
    not exist on the table being aggregated for a particular KPI, the
    service silently ignores that filter for that KPI (it never errors).
    The caller's role tier is appended to the cache key, so the same
    filter set across two tiers does not pollute caches.
    """

    date_from: date | None = Field(
        default=None,
        description="Inclusive lower bound on placed_at (UTC). Defaults to last-30-days when omitted.",
    )
    date_to: date | None = Field(
        default=None,
        description="Inclusive upper bound on placed_at (UTC). Defaults to today.",
    )
    city_id: str | None = Field(
        default=None,
        max_length=64,
        description="Free-text city code, matched against orders.delivery_address->>'city'.",
    )
    branch_id: str | None = Field(
        default=None,
        max_length=64,
        description="Warehouse / branch code (e.g. 'MAIN'). Applied where the column exists.",
    )
    seller_id: UUID | None = Field(
        default=None,
        description="Seller UUID. Applied to KPIs that read marketplace rows.",
    )
    category_id: UUID | None = Field(
        default=None,
        description="Catalog category UUID. Applied to KPIs that read catalog rows.",
    )

    @model_validator(mode="after")
    def _check_range(self) -> "KpiFilters":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


# ────────────────────────────────────────────────────────────────────────
# Output — chart primitives
# ────────────────────────────────────────────────────────────────────────


Severity = Literal["info", "warn", "error"]
Trend = Literal["up", "down", "flat"]


class KpiCard(StrictModel):
    """Single-metric stat card.

    ``value`` is always a string so the client can render currency,
    counts, and percentages without re-implementing locale rules.
    ``raw`` carries the underlying number for sort / threshold logic.
    """

    code: str
    label: str
    value: str
    raw: Decimal | None = None
    unit: str
    delta_pct: float | None = None
    trend: Trend = "flat"


class RoundBar(StrictModel):
    """Circular progress / gauge — e.g. delivery success rate."""

    code: str
    label: str
    percent: float  # 0..100
    severity: Severity = "info"
    caption: str | None = None


class HorizontalBarPoint(StrictModel):
    label: str
    value: Decimal
    color: str | None = None


class HorizontalBar(StrictModel):
    """Top-N comparison list — e.g. top-5 categories by GMV."""

    code: str
    label: str
    unit: str
    points: list[HorizontalBarPoint] = Field(default_factory=list)


class DonutSlice(StrictModel):
    label: str
    value: Decimal
    color: str | None = None


class DonutChart(StrictModel):
    """Categorical split — e.g. cod vs online."""

    code: str
    label: str
    unit: str
    slices: list[DonutSlice] = Field(default_factory=list)


class LinePoint(StrictModel):
    on: date
    value: Decimal


class LineChart(StrictModel):
    """Time-series — one bucket per day across [date_from, date_to]."""

    code: str
    label: str
    unit: str
    points: list[LinePoint] = Field(default_factory=list)


class Alert(StrictModel):
    """Actionable threshold breach the operator should clear."""

    code: str
    severity: Severity
    message: str
    action_href: str | None = None


class DeepLink(StrictModel):
    """Cross-link to the underlying admin surface that owns a metric."""

    code: str
    label: str
    href: str


class KpiDashboardResponse(StrictModel):
    """The single shape every role tier returns.

    Each section is always present (possibly empty). The frontend can
    render unconditionally without ``if section in response`` guards.
    """

    tier: Literal["staff", "supervisor", "admin", "super_admin"]
    date_from: date
    date_to: date
    kpi_cards: list[KpiCard] = Field(default_factory=list)
    round_bars: list[RoundBar] = Field(default_factory=list)
    horizontal_bars: list[HorizontalBar] = Field(default_factory=list)
    donut_charts: list[DonutChart] = Field(default_factory=list)
    line_charts: list[LineChart] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)
    deep_links: list[DeepLink] = Field(default_factory=list)
    cached: bool = False
