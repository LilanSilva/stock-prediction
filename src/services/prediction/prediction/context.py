"""Event-time context windowing helpers (pure functions)."""

from __future__ import annotations

from datetime import datetime, timedelta


def window_bounds(event_time: datetime, window_minutes: int) -> tuple[datetime, datetime]:
    """Return the tumbling ``[start, end)`` window (aligned to the UTC day) covering ``event_time``.

    Fixed, day-aligned windows make bucketing deterministic and idempotent: the same event time
    always maps to the same window regardless of arrival order.
    """
    day_start = event_time.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = (event_time - day_start).total_seconds() / 60.0
    bucket = int(elapsed_minutes // window_minutes)
    start = day_start + timedelta(minutes=bucket * window_minutes)
    end = start + timedelta(minutes=window_minutes)
    return start, end


def is_ready(window_end: datetime, now: datetime, grace_minutes: int) -> bool:
    """A context is ready to close once wall-clock time passes ``window_end`` plus the grace."""
    return now >= window_end + timedelta(minutes=grace_minutes)
