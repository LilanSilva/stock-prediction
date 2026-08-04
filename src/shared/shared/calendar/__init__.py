"""Shared market-session calendar, per-market timezone aware.

Used by Verification (baseline/settlement resolution), Market Data (session completion),
Prediction (market-open stance), and the offline structure learner. Each asset's timezone and
closing clock come from the asset registry, so every market is treated on its own calendar.
"""

from __future__ import annotations

from shared.calendar.exceptions import CalendarError, UnsupportedTimezoneError
from shared.calendar.sessions import (
    NEW_YORK,
    SESSION_COMPLETION_HOUR,
    SESSION_COMPLETION_MINUTE,
    is_session_complete,
    is_trading_day,
    local_date_in,
    market_offset,
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
    "SESSION_COMPLETION_MINUTE",
    "CalendarError",
    "UnsupportedTimezoneError",
    "is_session_complete",
    "is_trading_day",
    "local_date_in",
    "market_offset",
    "new_york_offset",
    "next_session",
    "previous_session",
    "provider_bar_to_session",
    "resolve_baseline_settlement",
    "session_completed_at",
]
