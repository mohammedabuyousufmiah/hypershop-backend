"""Cell-formatting helpers shared by all exporters.

Builders return native Python types (Decimal, date, UUID, ...) so the
JSON path can serialise correctly. The exporters cast each cell to a
string suited to the *file* format, with the column ``type`` driving
the formatting (e.g. money → 2dp, ratio → percent).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def format_cell(value: Any, col_type: str | None) -> str:
    """Format ``value`` per declared column type.

    Unknown types fall through to ``str(value)``. None becomes empty
    string (CSV/XLSX/PDF all treat empty as missing, which matches
    user expectation better than the literal "None").
    """
    if value is None:
        return ""
    t = col_type or "string"
    if t == "money":
        return _fmt_money(value)
    if t == "decimal":
        return _fmt_decimal(value)
    if t == "ratio":
        return _fmt_ratio(value)
    if t == "int":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return str(value)
    if t == "date":
        return _fmt_date(value)
    if t == "datetime":
        return _fmt_datetime(value)
    if t == "uuid":
        return _fmt_uuid(value)
    return str(value)


def _fmt_money(v: Any) -> str:
    if isinstance(v, Decimal):
        return f"{v:,.2f}"
    try:
        return f"{Decimal(str(v)):,.2f}"
    except Exception:  # noqa: BLE001
        return str(v)


def _fmt_decimal(v: Any) -> str:
    if isinstance(v, Decimal):
        return f"{v}"
    try:
        return f"{Decimal(str(v))}"
    except Exception:  # noqa: BLE001
        return str(v)


def _fmt_ratio(v: Any) -> str:
    """0.0234 → '2.34%'. Already-percent values (>=2) get formatted as-is."""
    try:
        d = Decimal(str(v))
    except Exception:  # noqa: BLE001
        return str(v)
    pct = (d * 100) if abs(d) <= Decimal("1") else d
    return f"{pct:.2f}%"


def _fmt_date(v: Any) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _fmt_datetime(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    return str(v)


def _fmt_uuid(v: Any) -> str:
    if isinstance(v, UUID):
        return str(v)
    return str(v)
