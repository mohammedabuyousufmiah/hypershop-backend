from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime received; all times must be timezone-aware UTC")
    return value.astimezone(UTC)


def utc_at(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def utc_in(seconds: int) -> datetime:
    return utc_now() + timedelta(seconds=seconds)
