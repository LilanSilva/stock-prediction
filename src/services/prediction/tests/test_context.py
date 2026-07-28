from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prediction.context import is_ready, window_bounds


def test_window_bounds_aligns_to_the_hour() -> None:
    event_time = datetime(2026, 7, 27, 14, 37, 12, tzinfo=UTC)
    start, end = window_bounds(event_time, 60)
    assert start == datetime(2026, 7, 27, 14, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 27, 15, 0, tzinfo=UTC)


def test_window_bounds_is_deterministic_across_arrival_order() -> None:
    a = datetime(2026, 7, 27, 14, 5, tzinfo=UTC)
    b = datetime(2026, 7, 27, 14, 55, tzinfo=UTC)
    assert window_bounds(a, 60) == window_bounds(b, 60)


def test_window_bounds_respects_window_size() -> None:
    event_time = datetime(2026, 7, 27, 14, 20, tzinfo=UTC)
    start, end = window_bounds(event_time, 30)
    assert start == datetime(2026, 7, 27, 14, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 27, 14, 30, tzinfo=UTC)


def test_is_ready_only_after_window_end_plus_grace() -> None:
    window_end = datetime(2026, 7, 27, 15, 0, tzinfo=UTC)
    assert is_ready(window_end, window_end + timedelta(minutes=4), 5) is False
    assert is_ready(window_end, window_end + timedelta(minutes=5), 5) is True
    assert is_ready(window_end, window_end + timedelta(minutes=6), 5) is True
