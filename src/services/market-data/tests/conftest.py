"""Shared test fixtures for the Market Data Service."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest


def _ts(year: int, month: int, day: int) -> int:
    """Unix seconds for a Yahoo daily bar stamped 21:00 UTC (17:00 America/New_York, EDT)."""
    return int(datetime(year, month, day, 21, 0, tzinfo=UTC).timestamp())


# Minimal valid Yahoo chart JSON for GOLD (GC=F / CMX / USD / America/New_York) with three sessions.
GOLD_CHART: dict[str, object] = {
    "chart": {
        "error": None,
        "result": [
            {
                "meta": {
                    "currency": "USD",
                    "exchangeName": "CMX",
                    "exchangeTimezoneName": "America/New_York",
                    "instrumentType": "FUTURE",
                },
                "timestamp": [_ts(2026, 7, 9), _ts(2026, 7, 10), _ts(2026, 7, 13)],
                "indicators": {
                    "quote": [
                        {"close": [3300.5, 3315.0, 3290.25]},
                    ]
                },
            }
        ],
    }
}


def make_client(handler: object) -> httpx.AsyncClient:
    """An httpx.AsyncClient backed by a MockTransport calling `handler(request)`."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport)


@pytest.fixture
def gold_chart() -> dict[str, object]:
    return GOLD_CHART
