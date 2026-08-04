"""Tests for the biquote adapter: bar parsing, isOpen exclusion, close filtering, session lookup."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal

import httpx
import pytest
from shared.reference import REGISTRY_VERSION
from shared.schemas.messages import AssetId, PriceKind

from market_data.adapters.biquote import BiquoteAdapter
from market_data.exceptions import (
    AdapterUnavailableError,
    InvalidObservationError,
    PriceNotYetAvailableError,
)
from tests.conftest import GOLD_OHLC, make_client


def _adapter_returning(payload: dict[str, object], status: int = 200) -> BiquoteAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return BiquoteAdapter(make_client(handler))


async def test_get_close_returns_validated_observation() -> None:
    adapter = _adapter_returning(GOLD_OHLC)
    obs = await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))
    assert obs.session == date(2026, 7, 13)
    assert obs.close == Decimal("3290.25")
    assert obs.provider_symbol == "XAUUSD"
    assert obs.price_kind == PriceKind.PROVIDER_DAILY_CLOSE
    assert obs.is_adjusted is False
    assert obs.source == "biquote.io"
    assert obs.registry_version == REGISTRY_VERSION


async def test_missing_session_raises_not_yet_available() -> None:
    adapter = _adapter_returning(GOLD_OHLC)
    with pytest.raises(PriceNotYetAvailableError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 14))


async def test_open_bar_is_excluded() -> None:
    # The still-forming bar (isOpen=true) must never become an observation, even on a matching date.
    payload: dict[str, object] = {
        "symbol": "XAUUSD",
        "interval": "1d",
        "bars": [
            {"openTime": "2026-07-14T00:00:00Z", "close": 3999.0, "isOpen": True},
            {"openTime": "2026-07-13T00:00:00Z", "close": 3290.25, "isOpen": False},
        ],
    }
    adapter = _adapter_returning(payload)
    with pytest.raises(PriceNotYetAvailableError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 14))


async def test_non_positive_and_null_closes_are_skipped() -> None:
    payload = copy.deepcopy(GOLD_OHLC)
    payload["bars"] = [
        {"openTime": "2026-07-09T00:00:00Z", "close": None, "isOpen": False},
        {"openTime": "2026-07-10T00:00:00Z", "close": -1.0, "isOpen": False},
        {"openTime": "2026-07-13T00:00:00Z", "close": 3290.25, "isOpen": False},
    ]
    adapter = _adapter_returning(payload)
    observations = await adapter.fetch_observations(AssetId.GOLD, date(2026, 7, 13))
    # Only the positive, finite close survives include-all filtering.
    assert len(observations) == 1
    assert observations[0].close == Decimal("3290.25")


async def test_missing_bars_block_is_terminal() -> None:
    adapter = _adapter_returning({"symbol": "XAUUSD", "interval": "1d"})
    with pytest.raises(InvalidObservationError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))


async def test_unparseable_open_time_is_terminal() -> None:
    payload: dict[str, object] = {
        "symbol": "XAUUSD",
        "interval": "1d",
        "bars": [{"openTime": "not-a-date", "close": 3290.25, "isOpen": False}],
    }
    adapter = _adapter_returning(payload)
    with pytest.raises(InvalidObservationError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))


async def test_http_error_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    adapter = BiquoteAdapter(make_client(handler))
    with pytest.raises(AdapterUnavailableError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))
