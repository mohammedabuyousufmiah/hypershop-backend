"""Tiny filter-parsing helpers shared by every builder.

Builders accept arbitrary JSON filter objects from the API. These
helpers normalise the most common shapes (date ranges, optional
warehouse, pagination caps) and apply safe defaults so each builder
stays terse.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


def _coerce_date(v: Any, fallback: date) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return fallback
    return fallback


def date_range(
    filters: dict[str, Any],
    *,
    default_days: int = 30,
) -> tuple[date, date]:
    """Resolve ``date_from`` + ``date_to`` from filter dict.

    Falls back to ``[today - default_days, today]``. Either bound may
    be supplied independently. Inputs may be ISO date strings or date
    objects (callers from Python code may pass dates directly).
    """
    today = date.today()
    default_from = today - timedelta(days=default_days)
    starts_on = _coerce_date(filters.get("date_from"), default_from)
    ends_on = _coerce_date(filters.get("date_to"), today)
    if starts_on > ends_on:
        starts_on, ends_on = ends_on, starts_on
    return starts_on, ends_on


def opt_str(filters: dict[str, Any], key: str) -> str | None:
    v = filters.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def opt_int(
    filters: dict[str, Any],
    key: str,
    *,
    default: int,
    min_value: int = 1,
    max_value: int = 10000,
) -> int:
    v = filters.get(key, default)
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, n))
