"""Shared test fixtures for the Market Data Service."""

from __future__ import annotations

import httpx
import pytest

# Minimal valid biquote OHLC JSON for XOM_NYSE (symbol "XOM") with three settled daily sessions.
# biquote stamps each daily bar at UTC midnight and flags the still-forming day with isOpen=true;
# these fixtures are all settled bars (isOpen=false), so the session date is the openTime calendar
# date. The prices are arbitrary — these tests exercise parsing and validation, not real quotes.
BIQUOTE_OHLC: dict[str, object] = {
    "symbol": "XOM",
    "interval": "1d",
    "bars": [
        {"openTime": "2026-07-09T00:00:00Z", "close": 3300.5, "isOpen": False},
        {"openTime": "2026-07-10T00:00:00Z", "close": 3315.0, "isOpen": False},
        {"openTime": "2026-07-13T00:00:00Z", "close": 3290.25, "isOpen": False},
    ],
}


def make_client(handler: object) -> httpx.AsyncClient:
    """An httpx.AsyncClient backed by a MockTransport calling `handler(request)`."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


@pytest.fixture
def gold_ohlc() -> dict[str, object]:
    return BIQUOTE_OHLC
