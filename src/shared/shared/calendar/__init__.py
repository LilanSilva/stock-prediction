"""Shared market-session calendar (frozen `poc6-yahoo-reference-v1` policy).

Used by Verification (baseline/settlement resolution) and Market Data (session completion).
"""

from __future__ import annotations

from shared.calendar.exceptions import CalendarError, UnsupportedTimezoneError
from shared.calendar.sessions import (
    NEW_YORK,
    SESSION_COMPLETION_HOUR,
    is_session_complete,
    is_trading_day,
    new_york_offset,
    next_session,
    previous_session,
    provider_bar_to_session,
    resolve_baseline_settlement,
    session_completed_at,
)

__all__ = [
    "NEW_YORK",
    "SESSION_COMPLETION_HOUR",
    "CalendarError",
    "UnsupportedTimezoneError",
    "is_session_complete",
    "is_trading_day",
    "new_york_offset",
    "next_session",
    "previous_session",
    "provider_bar_to_session",
    "resolve_baseline_settlement",
    "session_completed_at",
]
