"""compute_next_run — pure function, no DB."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.reporting.service import compute_next_run


def _utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def test_daily_advances_to_next_local_hour():
    # BD is UTC+6. After 03:00 UTC = 09:00 BD; "9 AM local" should
    # next-fire at 03:00 UTC tomorrow.
    after = _utc(2026, 5, 4, 4)
    nxt = compute_next_run(
        frequency="daily",
        run_hour_local=9,
        run_day_of_week=None,
        run_day_of_month=None,
        timezone_offset_hours=6,
        after=after,
    )
    assert nxt.hour == 3  # 9 AM BD = 3 AM UTC
    assert nxt.day == 5


def test_daily_today_if_target_in_future():
    # 02:00 UTC = 08:00 BD; 9 AM BD is later today (03:00 UTC).
    after = _utc(2026, 5, 4, 2)
    nxt = compute_next_run(
        frequency="daily",
        run_hour_local=9,
        run_day_of_week=None,
        run_day_of_month=None,
        timezone_offset_hours=6,
        after=after,
    )
    assert nxt.day == 4
    assert nxt.hour == 3


def test_weekly_picks_next_dow():
    # 2026-05-04 is a Monday (weekday=0). Asking for Friday (weekday=4)
    # at 09:00 BD should pick 2026-05-08 03:00 UTC.
    after = _utc(2026, 5, 4, 4)
    nxt = compute_next_run(
        frequency="weekly",
        run_hour_local=9,
        run_day_of_week=4,
        run_day_of_month=None,
        timezone_offset_hours=6,
        after=after,
    )
    assert nxt.day == 8
    assert nxt.month == 5


def test_monthly_advances_when_dom_already_passed():
    # If today is the 4th and the schedule wants the 1st of each month
    # at 09:00 BD, next run is the 1st of the NEXT month.
    after = _utc(2026, 5, 4, 4)
    nxt = compute_next_run(
        frequency="monthly",
        run_hour_local=9,
        run_day_of_week=None,
        run_day_of_month=1,
        timezone_offset_hours=6,
        after=after,
    )
    assert nxt.month == 6
    assert nxt.day == 1


def test_unknown_frequency_raises():
    with pytest.raises(Exception):
        compute_next_run(
            frequency="hourly",
            run_hour_local=9,
            run_day_of_week=None,
            run_day_of_month=None,
            timezone_offset_hours=6,
            after=_utc(2026, 5, 4),
        )
