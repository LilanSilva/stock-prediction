"""Shared market-session calendar for the reference-price policy (`biquote-reference-v1`).

The session semantics MUST be identical across Verification (which resolves baseline/settlement
session dates without look-ahead) and Market Data (which decides when a session is complete before
fetching its close), so the logic lives here once rather than being duplicated per service.

POC scope:
  - Only America/New_York is supported; any other timezone is rejected, never silently mishandled.
  - A trading session is a weekday (Mon-Fri). Holidays are handled downstream by the absence of a
    provider bar for that date (Market Data treats a missing bar as a non-session), so no static
    holiday list is maintained for the POC.
  - A session is "complete" at 17:00 America/New_York on its session date.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from shared.calendar.exceptions import UnsupportedTimezoneError

NEW_YORK = "America/New_York"
SESSION_COMPLETION_HOUR = 17


def _require_supported_timezone(timezone_name: str) -> None:
    if timezone_name != NEW_YORK:
        raise UnsupportedTimezoneError(f"unsupported POC timezone: {timezone_name!r}")


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def new_york_offset(local_date: date) -> timedelta:
    """US DST offset for America/New_York (DST from 2nd Sun of March to 1st Sun of November)."""
    dst_start = _nth_weekday(local_date.year, 3, 6, 2)
    dst_end = _nth_weekday(local_date.year, 11, 6, 1)
    return timedelta(hours=-4 if dst_start <= local_date < dst_end else -5)


def provider_bar_to_session(provider_bar_time: datetime, timezone_name: str) -> date:
    """Map a provider bar UTC timestamp to its local-market session date."""
    _require_supported_timezone(timezone_name)
    as_utc = provider_bar_time.astimezone(UTC)
    return (as_utc + new_york_offset(as_utc.date())).date()


def session_completed_at(session: date, timezone_name: str) -> datetime:
    """UTC instant at which the given local session is considered complete (17:00 local)."""
    _require_supported_timezone(timezone_name)
    local_naive = datetime.combine(session, time(SESSION_COMPLETION_HOUR, 0))
    return (local_naive - new_york_offset(session)).replace(tzinfo=UTC)


def is_session_complete(session: date, timezone_name: str, *, now: datetime) -> bool:
    """True when `now` is at or after the session's completion instant."""
    return now >= session_completed_at(session, timezone_name)


def is_trading_day(day: date) -> bool:
    """Weekday sessions only (Mon-Fri) for the POC calendar."""
    return day.weekday() < 5


def previous_session(day: date) -> date:
    """The trading session strictly before `day`."""
    cursor = day - timedelta(days=1)
    while not is_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def next_session(day: date) -> date:
    """The trading session strictly after `day`."""
    cursor = day + timedelta(days=1)
    while not is_trading_day(cursor):
        cursor += timedelta(days=1)
    return cursor


def resolve_baseline_settlement(
    decision_at: datetime, timezone_name: str
) -> tuple[date, date]:
    """Resolve (baseline_session, settlement_session) for a prediction without look-ahead.

    Baseline is the most recent trading session already complete at ``decision_at``; settlement is
    the next trading session (the ONE_TRADING_DAY horizon).
    """
    _require_supported_timezone(timezone_name)
    as_utc = decision_at.astimezone(UTC)
    local_date = (as_utc + new_york_offset(as_utc.date())).date()

    baseline = local_date
    while not is_trading_day(baseline):
        baseline -= timedelta(days=1)
    # No look-ahead: if the candidate session has not closed yet, use the prior completed session.
    if not is_session_complete(baseline, timezone_name, now=as_utc):
        baseline = previous_session(baseline)

    settlement = next_session(baseline)
    return baseline, settlement
