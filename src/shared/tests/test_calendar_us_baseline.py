"""Characterisation tests pinning US session behaviour across the ZoneInfo refactor.

Written against the hand-rolled DST arithmetic BEFORE replacing it with stdlib ZoneInfo, so any
change in US results fails loudly. A subtle calendar error would otherwise grade predictions against
the wrong sessions silently, which is far worse than a crash.

The expected values here are the pre-refactor outputs, verified against the real US DST rules:
EDT (UTC-4) from the 2nd Sunday in March to the 1st Sunday in November, EST (UTC-5) otherwise.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from shared.calendar import (
    NEW_YORK,
    is_session_complete,
    is_trading_day,
    next_session,
    previous_session,
    provider_bar_to_session,
    resolve_baseline_settlement,
    session_completed_at,
)

# --- DST offset boundaries ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("session", "expected_utc"),
    [
        # Deep winter: EST, UTC-5 -> 17:00 local == 22:00 UTC.
        (date(2026, 1, 15), datetime(2026, 1, 15, 22, 0, tzinfo=UTC)),
        # Day before DST starts (2nd Sunday of March 2026 is the 8th): still EST.
        (date(2026, 3, 6), datetime(2026, 3, 6, 22, 0, tzinfo=UTC)),
        # First weekday after DST starts: EDT, UTC-4 -> 17:00 local == 21:00 UTC.
        (date(2026, 3, 9), datetime(2026, 3, 9, 21, 0, tzinfo=UTC)),
        # Deep summer: EDT.
        (date(2026, 7, 15), datetime(2026, 7, 15, 21, 0, tzinfo=UTC)),
        # Day before DST ends (1st Sunday of November 2026 is the 1st): back to EST.
        (date(2026, 10, 30), datetime(2026, 10, 30, 21, 0, tzinfo=UTC)),
        (date(2026, 11, 2), datetime(2026, 11, 2, 22, 0, tzinfo=UTC)),
    ],
)
def test_session_completion_instant_tracks_us_dst(session: date, expected_utc: datetime) -> None:
    assert session_completed_at(session, NEW_YORK) == expected_utc


def test_is_session_complete_is_exact_at_the_boundary() -> None:
    completion = session_completed_at(date(2026, 7, 15), NEW_YORK)
    assert is_session_complete(date(2026, 7, 15), NEW_YORK, now=completion) is True
    assert (
        is_session_complete(
            date(2026, 7, 15), NEW_YORK, now=completion - timedelta(seconds=1)
        )
        is False
    )


# --- trading-day arithmetic ---------------------------------------------------------------------


def test_weekday_rule() -> None:
    assert is_trading_day(date(2026, 7, 13)) is True  # Monday
    assert is_trading_day(date(2026, 7, 17)) is True  # Friday
    assert is_trading_day(date(2026, 7, 18)) is False  # Saturday
    assert is_trading_day(date(2026, 7, 19)) is False  # Sunday


def test_previous_and_next_session_skip_weekends() -> None:
    monday = date(2026, 7, 13)
    friday = date(2026, 7, 10)
    assert previous_session(monday) == friday
    assert next_session(friday) == monday
    # Strictly before/after, never the day itself.
    assert previous_session(friday) == date(2026, 7, 9)
    assert next_session(monday) == date(2026, 7, 14)


# --- baseline / settlement resolution -----------------------------------------------------------


@pytest.mark.parametrize(
    ("decision_at", "baseline", "settlement"),
    [
        # Wednesday 22:00 UTC (18:00 EDT) -- Wednesday's session has closed, so it is the baseline.
        (datetime(2026, 7, 15, 22, 0, tzinfo=UTC), date(2026, 7, 15), date(2026, 7, 16)),
        # Wednesday 14:00 UTC (10:00 EDT) -- not closed yet, so no look-ahead: use Tuesday.
        (datetime(2026, 7, 15, 14, 0, tzinfo=UTC), date(2026, 7, 14), date(2026, 7, 15)),
        # Saturday -- roll back to Friday, settle on Monday.
        (datetime(2026, 7, 18, 12, 0, tzinfo=UTC), date(2026, 7, 17), date(2026, 7, 20)),
        # Sunday -- same as Saturday.
        (datetime(2026, 7, 19, 12, 0, tzinfo=UTC), date(2026, 7, 17), date(2026, 7, 20)),
        # Monday morning before the close -- baseline is the previous Friday.
        (datetime(2026, 7, 20, 13, 0, tzinfo=UTC), date(2026, 7, 17), date(2026, 7, 20)),
        # Winter (EST): Wednesday 22:00 UTC is exactly 17:00 EST, so Wednesday counts as closed.
        (datetime(2026, 1, 14, 22, 0, tzinfo=UTC), date(2026, 1, 14), date(2026, 1, 15)),
        # Winter, one hour earlier: not closed, fall back to Tuesday.
        (datetime(2026, 1, 14, 21, 0, tzinfo=UTC), date(2026, 1, 13), date(2026, 1, 14)),
    ],
)
def test_baseline_settlement_has_no_look_ahead(
    decision_at: datetime, baseline: date, settlement: date
) -> None:
    assert resolve_baseline_settlement(decision_at, NEW_YORK) == (baseline, settlement)


def test_settlement_is_always_a_trading_day_after_baseline() -> None:
    for day in range(1, 29):
        decision = datetime(2026, 7, day, 12, 0, tzinfo=UTC)
        baseline, settlement = resolve_baseline_settlement(decision, NEW_YORK)
        assert is_trading_day(baseline)
        assert is_trading_day(settlement)
        assert settlement > baseline


# --- provider bar mapping -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bar_time", "expected_session"),
    [
        # biquote stamps daily bars at UTC midnight; in New York that is the previous evening.
        (datetime(2026, 7, 15, 0, 0, tzinfo=UTC), date(2026, 7, 14)),
        (datetime(2026, 7, 15, 20, 0, tzinfo=UTC), date(2026, 7, 15)),
        # Winter: UTC-5 shifts the boundary an hour later.
        (datetime(2026, 1, 15, 4, 0, tzinfo=UTC), date(2026, 1, 14)),
        (datetime(2026, 1, 15, 6, 0, tzinfo=UTC), date(2026, 1, 15)),
    ],
)
def test_provider_bar_maps_to_local_session_date(
    bar_time: datetime, expected_session: date
) -> None:
    assert provider_bar_to_session(bar_time, NEW_YORK) == expected_session
