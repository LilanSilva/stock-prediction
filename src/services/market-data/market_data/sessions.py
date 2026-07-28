"""Session-calendar logic for the frozen `poc6-yahoo-reference-v1` policy.

Ported from src/poc/poc6/market_policy.py (the P06/T03-approved implementation) so the service
reproduces the exact session semantics that were validated:

  - Provider bar UTC timestamps are mapped to a local America/New_York session date.
  - A session is "complete" at 17:00 America/New_York on its session date.
  - Baseline is the latest completed session; settlement is the next returned session after it.
  - Absence of a session from the returned provider series means it is a non-session (holiday/gap).

Only America/New_York is supported for the POC assets; any other timezone is rejected rather than
silently mishandled.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from market_data.exceptions import InvalidObservationError

_NEW_YORK = "America/New_York"


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def new_york_offset(local_date: date) -> timedelta:
    """US DST offset used by America/New_York (DST from 2nd Sun of March to 1st Sun of November)."""
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
    local_naive = datetime.combine(session, time(17, 0))
    return (local_naive - new_york_offset(session)).replace(tzinfo=UTC)


def is_session_complete(session: date, timezone_name: str, *, now: datetime) -> bool:
    """True when `now` is at or after the session's completion instant."""
    return now >= session_completed_at(session, timezone_name)


def _require_supported_timezone(timezone_name: str) -> None:
    if timezone_name != _NEW_YORK:
        raise InvalidObservationError(f"unsupported POC timezone: {timezone_name!r}")
