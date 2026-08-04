"""Shared market-session calendar, per-market timezone aware.

The session semantics MUST be identical across Verification (which resolves baseline/settlement
session dates without look-ahead) and Market Data (which decides when a session is complete before
fetching its close), so the logic lives here once rather than being duplicated per service.

Scope:
  - Any IANA timezone is supported, resolved through stdlib ``zoneinfo``. Each asset declares its
    own ``timezone`` and ``session_complete_at`` in the registry, so a Stockholm listing closes on
    Stockholm's clock and a New York listing on New York's. DST is handled by the tz database rather
    than by hand: the US and EU switch on different dates, so hardcoded rules cannot serve both.
  - A trading session is a weekday (Mon-Fri). Holidays are handled downstream by the absence of a
    provider bar for that date (Market Data treats a missing bar as a non-session), so no static
    holiday list is maintained. This applies to every market equally: a Stockholm holiday behaves
    exactly as a US holiday already does.
  - A session is "complete" at its market's local closing wall-clock on the session date, defaulting
    to 17:00 when a caller does not pass one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shared.calendar.exceptions import UnsupportedTimezoneError

NEW_YORK = "America/New_York"

# Default local wall-clock at which a session's daily close is treated as final. Per-market values
# come from the asset registry (`session_complete_at`); this is only the fallback.
SESSION_COMPLETION_HOUR = 17
SESSION_COMPLETION_MINUTE = 0


def _zone(timezone_name: str) -> ZoneInfo:
    """Resolve an IANA timezone, rejecting an unknown name rather than silently mishandling it."""
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnsupportedTimezoneError(f"unknown timezone: {timezone_name!r}") from exc


def market_offset(local_date: date, timezone_name: str = NEW_YORK) -> timedelta:
    """UTC offset for ``timezone_name`` on ``local_date`` (DST resolved from the tz database)."""
    zone = _zone(timezone_name)
    offset = datetime.combine(local_date, time(12, 0), tzinfo=zone).utcoffset()
    # A zone-aware datetime always has an offset; the guard keeps the return type honest.
    return offset if offset is not None else timedelta(0)


def new_york_offset(local_date: date) -> timedelta:
    """US DST offset for America/New_York. Retained for existing callers."""
    return market_offset(local_date, NEW_YORK)


def provider_bar_to_session(provider_bar_time: datetime, timezone_name: str) -> date:
    """Map a provider bar UTC timestamp to its local-market session date."""
    return provider_bar_time.astimezone(_zone(timezone_name)).date()


def session_completed_at(
    session: date,
    timezone_name: str,
    *,
    hour: int = SESSION_COMPLETION_HOUR,
    minute: int = SESSION_COMPLETION_MINUTE,
) -> datetime:
    """UTC instant at which the given local session is complete (local ``hour:minute``)."""
    local = datetime.combine(session, time(hour, minute), tzinfo=_zone(timezone_name))
    return local.astimezone(UTC)


def is_session_complete(
    session: date,
    timezone_name: str,
    *,
    now: datetime,
    hour: int = SESSION_COMPLETION_HOUR,
    minute: int = SESSION_COMPLETION_MINUTE,
) -> bool:
    """True when ``now`` is at or after the session's completion instant."""
    return now >= session_completed_at(session, timezone_name, hour=hour, minute=minute)


def is_trading_day(day: date) -> bool:
    """Weekday sessions only (Mon-Fri); holidays surface as a missing provider bar."""
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


def local_date_in(moment: datetime, timezone_name: str) -> date:
    """The calendar date ``moment`` falls on in ``timezone_name``."""
    return moment.astimezone(_zone(timezone_name)).date()


def resolve_baseline_settlement(
    decision_at: datetime,
    timezone_name: str,
    *,
    hour: int = SESSION_COMPLETION_HOUR,
    minute: int = SESSION_COMPLETION_MINUTE,
) -> tuple[date, date]:
    """Resolve (baseline_session, settlement_session) for a prediction without look-ahead.

    Baseline is the most recent trading session already complete at ``decision_at``; settlement is
    the next trading session (the ONE_TRADING_DAY horizon). Both are dates on the asset's own market
    calendar, so a Stockholm prediction settles against Stockholm sessions.
    """
    as_utc = decision_at.astimezone(UTC)
    local_date = local_date_in(as_utc, timezone_name)

    baseline = local_date
    while not is_trading_day(baseline):
        baseline -= timedelta(days=1)
    # No look-ahead: if the candidate session has not closed yet, use the prior completed session.
    if not is_session_complete(
        baseline, timezone_name, now=as_utc, hour=hour, minute=minute
    ):
        baseline = previous_session(baseline)

    settlement = next_session(baseline)
    return baseline, settlement
