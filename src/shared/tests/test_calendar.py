from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from shared.calendar import (
    is_session_complete,
    is_trading_day,
    next_session,
    previous_session,
    resolve_baseline_settlement,
    session_completed_at,
)
from shared.calendar.exceptions import UnsupportedTimezoneError

NY = "America/New_York"


def test_trading_day_is_weekday_only() -> None:
    assert is_trading_day(date(2026, 7, 27)) is True  # Monday
    assert is_trading_day(date(2026, 7, 25)) is False  # Saturday
    assert is_trading_day(date(2026, 7, 26)) is False  # Sunday


def test_previous_and_next_session_skip_weekends() -> None:
    monday = date(2026, 7, 27)
    friday = date(2026, 7, 24)
    tuesday = date(2026, 7, 28)
    assert previous_session(monday) == friday
    assert next_session(friday) == monday
    assert next_session(monday) == tuesday


def test_session_completed_at_is_17_local() -> None:
    # July -> EDT (UTC-4): 17:00 local == 21:00 UTC.
    completed = session_completed_at(date(2026, 7, 27), NY)
    assert completed == datetime(2026, 7, 27, 21, 0, tzinfo=UTC)


def test_is_session_complete_boundary() -> None:
    session = date(2026, 7, 27)
    assert is_session_complete(session, NY, now=datetime(2026, 7, 27, 20, 59, tzinfo=UTC)) is False
    assert is_session_complete(session, NY, now=datetime(2026, 7, 27, 21, 0, tzinfo=UTC)) is True


def test_resolve_after_close_uses_same_day_baseline() -> None:
    # Monday 22:46 UTC = 18:46 EDT, after the 17:00 close -> baseline Monday, settlement Tuesday.
    baseline, settlement = resolve_baseline_settlement(
        datetime(2026, 7, 27, 22, 46, tzinfo=UTC), NY
    )
    assert baseline == date(2026, 7, 27)
    assert settlement == date(2026, 7, 28)


def test_resolve_before_close_steps_back_no_look_ahead() -> None:
    # Monday 14:00 UTC = 10:00 EDT, before close -> last completed is Friday, settlement Monday.
    baseline, settlement = resolve_baseline_settlement(
        datetime(2026, 7, 27, 14, 0, tzinfo=UTC), NY
    )
    assert baseline == date(2026, 7, 24)
    assert settlement == date(2026, 7, 27)


def test_resolve_on_weekend_uses_prior_friday() -> None:
    baseline, settlement = resolve_baseline_settlement(
        datetime(2026, 7, 26, 12, 0, tzinfo=UTC), NY  # Sunday
    )
    assert baseline == date(2026, 7, 24)  # Friday
    assert settlement == date(2026, 7, 27)  # Monday


def test_unsupported_timezone_rejected() -> None:
    with pytest.raises(UnsupportedTimezoneError):
        resolve_baseline_settlement(datetime(2026, 7, 27, 22, 0, tzinfo=UTC), "Europe/Stockholm")
