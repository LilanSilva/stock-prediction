"""Tests for the frozen session-calendar logic."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from market_data.exceptions import InvalidObservationError
from market_data.sessions import (
    is_session_complete,
    new_york_offset,
    provider_bar_to_session,
    session_completed_at,
)

_NY = "America/New_York"


def test_new_york_offset_edt_and_est() -> None:
    # July is EDT (-4h); January is EST (-5h).
    assert new_york_offset(date(2026, 7, 15)).total_seconds() == -4 * 3600
    assert new_york_offset(date(2026, 1, 15)).total_seconds() == -5 * 3600


def test_provider_bar_maps_to_local_session() -> None:
    # 21:00 UTC on 2026-07-13 is 17:00 EDT the same day.
    bar = datetime(2026, 7, 13, 21, 0, tzinfo=UTC)
    assert provider_bar_to_session(bar, _NY) == date(2026, 7, 13)


def test_session_completed_at_is_17_local() -> None:
    completed = session_completed_at(date(2026, 7, 13), _NY)
    # 17:00 EDT == 21:00 UTC.
    assert completed == datetime(2026, 7, 13, 21, 0, tzinfo=UTC)


def test_is_session_complete_boundary() -> None:
    session = date(2026, 7, 13)
    just_before = datetime(2026, 7, 13, 20, 59, tzinfo=UTC)
    at_close = datetime(2026, 7, 13, 21, 0, tzinfo=UTC)
    assert is_session_complete(session, _NY, now=just_before) is False
    assert is_session_complete(session, _NY, now=at_close) is True


def test_unsupported_timezone_rejected() -> None:
    with pytest.raises(InvalidObservationError):
        session_completed_at(date(2026, 7, 13), "Europe/London")
