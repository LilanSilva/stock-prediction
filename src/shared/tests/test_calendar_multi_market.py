"""Per-market session calendar tests for the non-US markets in the asset registry.

The bug these guard against is silent, not loud: if a Stockholm close is mapped to a New York
session, every Swedish prediction is graded against the wrong two days and still looks plausible.

Note that EU and US daylight saving switch on *different* dates (EU: last Sunday of March/October;
US: 2nd Sunday of March, 1st Sunday of November), which is why the offsets are resolved from the tz
database rather than hardcoded.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from shared.calendar import (
    NEW_YORK,
    is_session_complete,
    local_date_in,
    market_offset,
    provider_bar_to_session,
    resolve_baseline_settlement,
    session_completed_at,
)
from shared.reference import resolve, supported_assets

STOCKHOLM = "Europe/Stockholm"
COPENHAGEN = "Europe/Copenhagen"
AMSTERDAM = "Europe/Amsterdam"
PARIS = "Europe/Paris"
BERLIN = "Europe/Berlin"


# --- offsets ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("timezone_name", "summer_hours", "winter_hours"),
    [
        (STOCKHOLM, 2, 1),
        (COPENHAGEN, 2, 1),
        (AMSTERDAM, 2, 1),
        (PARIS, 2, 1),
        (BERLIN, 2, 1),
        (NEW_YORK, -4, -5),
    ],
)
def test_market_offsets_follow_each_zone_own_dst(
    timezone_name: str, summer_hours: int, winter_hours: int
) -> None:
    assert market_offset(date(2026, 7, 15), timezone_name) == timedelta(hours=summer_hours)
    assert market_offset(date(2026, 1, 15), timezone_name) == timedelta(hours=winter_hours)


def test_eu_and_us_dst_transitions_differ() -> None:
    # 2026-03-10 falls after the US switch (8th) but before the EU switch (29th). A single hardcoded
    # DST rule cannot serve both markets, which is why zoneinfo does this.
    mid_march = date(2026, 3, 10)
    assert market_offset(mid_march, NEW_YORK) == timedelta(hours=-4)  # already EDT
    assert market_offset(mid_march, STOCKHOLM) == timedelta(hours=1)  # still CET


# --- session completion -------------------------------------------------------------------------


def test_stockholm_closes_hours_before_new_york() -> None:
    session = date(2026, 7, 15)
    stockholm_close = session_completed_at(session, STOCKHOLM, hour=18)
    new_york_close = session_completed_at(session, NEW_YORK, hour=17)
    assert stockholm_close == datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
    assert new_york_close == datetime(2026, 7, 15, 21, 0, tzinfo=UTC)
    # Using New York's clock for a Swedish asset would delay its close by 5 hours.
    assert new_york_close - stockholm_close == timedelta(hours=5)


def test_stockholm_session_is_complete_on_its_own_clock() -> None:
    session = date(2026, 7, 15)
    # 16:00 UTC is exactly 18:00 in Stockholm.
    assert is_session_complete(
        session, STOCKHOLM, now=datetime(2026, 7, 15, 16, 0, tzinfo=UTC), hour=18
    )
    assert not is_session_complete(
        session, STOCKHOLM, now=datetime(2026, 7, 15, 15, 59, tzinfo=UTC), hour=18
    )
    # The same instant is NOT yet a completed New York session.
    assert not is_session_complete(
        session, NEW_YORK, now=datetime(2026, 7, 15, 16, 0, tzinfo=UTC), hour=17
    )


def test_winter_close_shifts_with_dst() -> None:
    # January: CET (UTC+1), so an 18:00 local close is 17:00 UTC rather than 16:00.
    assert session_completed_at(date(2026, 1, 15), STOCKHOLM, hour=18) == datetime(
        2026, 1, 15, 17, 0, tzinfo=UTC
    )


# --- baseline / settlement ----------------------------------------------------------------------


def test_stockholm_baseline_settles_on_stockholm_sessions() -> None:
    # Wednesday 17:00 UTC (19:00 CEST) -- Wednesday has closed in Stockholm.
    baseline, settlement = resolve_baseline_settlement(
        datetime(2026, 7, 15, 17, 0, tzinfo=UTC), STOCKHOLM, hour=18
    )
    assert (baseline, settlement) == (date(2026, 7, 15), date(2026, 7, 16))


def test_same_instant_resolves_differently_per_market() -> None:
    # 17:00 UTC on a Wednesday: Stockholm has closed, New York has not. The same prediction time
    # therefore grades against different sessions depending on the listing.
    moment = datetime(2026, 7, 15, 17, 0, tzinfo=UTC)
    assert resolve_baseline_settlement(moment, STOCKHOLM, hour=18)[0] == date(2026, 7, 15)
    assert resolve_baseline_settlement(moment, NEW_YORK, hour=17)[0] == date(2026, 7, 14)


def test_weekend_rolls_back_to_friday_in_every_market() -> None:
    saturday = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    for timezone_name in (STOCKHOLM, COPENHAGEN, PARIS, AMSTERDAM, BERLIN, NEW_YORK):
        baseline, settlement = resolve_baseline_settlement(saturday, timezone_name)
        assert baseline == date(2026, 7, 17)  # Friday
        assert settlement == date(2026, 7, 20)  # Monday


def test_late_evening_utc_can_be_the_next_local_day_in_europe() -> None:
    # 23:00 UTC on a Wednesday is already Thursday 01:00 in Stockholm; the local date drives the
    # session, so Thursday has not closed and Wednesday is the baseline.
    moment = datetime(2026, 7, 15, 23, 0, tzinfo=UTC)
    assert local_date_in(moment, STOCKHOLM) == date(2026, 7, 16)
    baseline, _ = resolve_baseline_settlement(moment, STOCKHOLM, hour=18)
    assert baseline == date(2026, 7, 15)


# --- provider bar mapping -----------------------------------------------------------------------


def test_provider_bar_maps_to_the_local_session_date() -> None:
    # A bar stamped at UTC midnight is already 02:00 the same day in Stockholm (CEST), so it belongs
    # to that date -- unlike New York, where UTC midnight is the previous evening.
    bar = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
    assert provider_bar_to_session(bar, STOCKHOLM) == date(2026, 7, 15)
    assert provider_bar_to_session(bar, NEW_YORK) == date(2026, 7, 14)


# --- registry consistency -----------------------------------------------------------------------


def test_every_registered_asset_has_a_usable_calendar() -> None:
    # A timezone typo in assets.json must surface here, not as a runtime failure mid-scoring.
    for asset_id in supported_assets():
        series = resolve(asset_id)
        completed = session_completed_at(
            date(2026, 7, 15),
            series.timezone,
            hour=series.session_completion_hour,
            minute=series.session_completion_minute,
        )
        assert completed.tzinfo is not None
        baseline, settlement = resolve_baseline_settlement(
            datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
            series.timezone,
            hour=series.session_completion_hour,
            minute=series.session_completion_minute,
        )
        assert settlement > baseline
