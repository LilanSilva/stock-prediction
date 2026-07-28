"""Typed errors for the shared market-session calendar."""

from __future__ import annotations


class CalendarError(Exception):
    """Base class for session-calendar errors."""


class UnsupportedTimezoneError(CalendarError):
    """Raised for a market timezone the POC calendar does not support."""
