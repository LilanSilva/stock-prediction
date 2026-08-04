"""Session-calendar shim delegating to the shared calendar.

The session math now lives in ``shared.calendar`` so Verification and Market Data share one
implementation. This module keeps Market Data's public function names and translates the shared
``UnsupportedTimezoneError`` into the service-local ``InvalidObservationError`` for DLQ routing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from shared.calendar import SESSION_COMPLETION_HOUR, SESSION_COMPLETION_MINUTE
from shared.calendar import new_york_offset as _new_york_offset
from shared.calendar import provider_bar_to_session as _provider_bar_to_session
from shared.calendar import session_completed_at as _session_completed_at
from shared.calendar.exceptions import UnsupportedTimezoneError

from market_data.exceptions import InvalidObservationError


def new_york_offset(local_date: date) -> timedelta:
    return _new_york_offset(local_date)


def provider_bar_to_session(provider_bar_time: datetime, timezone_name: str) -> date:
    try:
        return _provider_bar_to_session(provider_bar_time, timezone_name)
    except UnsupportedTimezoneError as exc:
        raise InvalidObservationError(str(exc)) from exc


def session_completed_at(
    session: date,
    timezone_name: str,
    *,
    hour: int = SESSION_COMPLETION_HOUR,
    minute: int = SESSION_COMPLETION_MINUTE,
) -> datetime:
    try:
        return _session_completed_at(session, timezone_name, hour=hour, minute=minute)
    except UnsupportedTimezoneError as exc:
        raise InvalidObservationError(str(exc)) from exc


def is_session_complete(
    session: date,
    timezone_name: str,
    *,
    now: datetime,
    hour: int = SESSION_COMPLETION_HOUR,
    minute: int = SESSION_COMPLETION_MINUTE,
) -> bool:
    """True once the session's local close has passed, on that market's own clock."""
    return now >= session_completed_at(session, timezone_name, hour=hour, minute=minute)
