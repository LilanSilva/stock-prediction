"""Tests for the Yahoo chart adapter: meta validation, close filtering, and session lookup."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal

import httpx
import pytest
from shared.schemas.messages import AssetId, PriceKind

from market_data.adapters.yahoo_chart import YahooChartAdapter
from market_data.exceptions import (
    AdapterUnavailableError,
    InvalidObservationError,
    PriceNotYetAvailableError,
)
from tests.conftest import GOLD_CHART, make_client


def _adapter_returning(payload: dict[str, object], status: int = 200) -> YahooChartAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return YahooChartAdapter(make_client(handler))


async def test_get_close_returns_validated_observation() -> None:
    adapter = _adapter_returning(GOLD_CHART)
    obs = await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))
    assert obs.session == date(2026, 7, 13)
    assert obs.close == Decimal("3290.25")
    assert obs.provider_symbol == "GC=F"
    assert obs.price_kind == PriceKind.PROVIDER_DAILY_CLOSE
    assert obs.is_adjusted is False
    assert obs.source == "Yahoo Finance chart"
    assert obs.registry_version == "poc6-yahoo-reference-v1"


async def test_missing_session_raises_not_yet_available() -> None:
    adapter = _adapter_returning(GOLD_CHART)
    with pytest.raises(PriceNotYetAvailableError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 14))


async def test_wrong_currency_is_terminal() -> None:
    payload = copy.deepcopy(GOLD_CHART)
    payload["chart"]["result"][0]["meta"]["currency"] = "EUR"  # type: ignore[index]
    adapter = _adapter_returning(payload)
    with pytest.raises(InvalidObservationError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))


async def test_wrong_exchange_is_terminal() -> None:
    payload = copy.deepcopy(GOLD_CHART)
    payload["chart"]["result"][0]["meta"]["exchangeName"] = "NYM"  # type: ignore[index]
    adapter = _adapter_returning(payload)
    with pytest.raises(InvalidObservationError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))


async def test_non_positive_and_null_closes_are_skipped() -> None:
    payload = copy.deepcopy(GOLD_CHART)
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [  # type: ignore[index]
        None,
        -1.0,
        3290.25,
    ]
    adapter = _adapter_returning(payload)
    observations = await adapter.fetch_observations(AssetId.GOLD, date(2026, 7, 13))
    # Only the last (positive, finite) close survives include-all filtering.
    assert len(observations) == 1
    assert observations[0].close == Decimal("3290.25")


async def test_provider_error_is_terminal() -> None:
    payload: dict[str, object] = {"chart": {"error": {"code": "Not Found"}, "result": None}}
    adapter = _adapter_returning(payload)
    with pytest.raises(InvalidObservationError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))


async def test_http_error_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    adapter = YahooChartAdapter(make_client(handler))
    with pytest.raises(AdapterUnavailableError):
        await adapter.get_close(AssetId.GOLD, date(2026, 7, 13))
