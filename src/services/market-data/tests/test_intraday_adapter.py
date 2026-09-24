import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from shared.reference import AssetReferenceSeries
from shared.schemas.messages import IntradayBar, IntradayRequested

from market_data.exceptions import AdapterUnavailableError, InvalidObservationError
from market_data.intraday_adapter import IntradayAdapter

START = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)


@pytest.fixture
def series() -> AssetReferenceSeries:
    return AssetReferenceSeries(
        asset_id="TEST_STOCK",
        provider="yahoo",
        provider_symbol="TEST",
        economic_identity="test",
        expected_exchange="TEST",
        timezone="America/New_York",
        currency="USD",
        price_kind="PROVIDER_DAILY_CLOSE",
        is_adjusted=False,
        rollover_policy="test",
        fallback=None,
        registry_version="test-v1",
        session_completion_hour=17,
        session_completion_minute=0,
        code="TEST:TEST",
        display_name="Test",
        group_id="TEST",
    )


@pytest.fixture
def request_message(monkeypatch: pytest.MonkeyPatch) -> IntradayRequested:
    monkeypatch.setattr(
        "shared.schemas.asset_id.is_known_asset", lambda value: value == "TEST_STOCK"
    )
    return IntradayRequested(
        correlation_id=uuid.uuid4(),
        occurred_at=START,
        stream_id=uuid.uuid4(),
        asset_id="TEST_STOCK",
        registry_version="test-v1",
        calendar_id="XNYS",
        opens_at=START,
        closes_at=START + timedelta(hours=6, minutes=30),
    )


def payload() -> dict[str, Any]:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "TEST",
                        "currency": "USD",
                        "exchangeTimezoneName": "America/New_York",
                    },
                    "timestamp": [
                        int((START + timedelta(minutes=i)).timestamp()) for i in range(3)
                    ],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100, None, 100],
                                "high": [101, None, 101],
                                "low": [99, None, 99],
                                "close": [100, None, 100],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


async def test_only_complete_valid_bars_and_browser_header(
    series: AssetReferenceSeries,
    request_message: IntradayRequested,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["interval"] == "1m"
        assert request.url.params["includePrePost"] == "false"
        assert "Mozilla" in request.headers["User-Agent"]
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await IntradayAdapter(client, "https://example.test/chart").fetch(
            request_message, series, START + timedelta(minutes=2, seconds=30)
        )
    assert len(result) == 1
    assert result[0].start == START


@pytest.mark.parametrize("defect", ["symbol", "currency", "identity", "split", "ohlc", "length"])
async def test_rejects_bad_provider_evidence(
    series: AssetReferenceSeries,
    request_message: IntradayRequested,
    defect: str,
) -> None:
    data = payload()
    result = data["chart"]["result"][0]
    if defect == "symbol":
        result["meta"]["symbol"] = "WRONG"
    elif defect == "currency":
        result["meta"]["currency"] = "EUR"
    elif defect == "identity":
        del result["meta"]["currency"]
    elif defect == "split":
        result["events"] = {"splits": {"test": {"splitRatio": "2:1"}}}
    elif defect == "ohlc":
        result["indicators"]["quote"][0]["high"][0] = 90
    else:
        result["indicators"]["quote"][0]["low"].pop()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    ) as client:
        with pytest.raises(InvalidObservationError):
            await IntradayAdapter(client, "https://example.test/chart").fetch(
                request_message, series, START + timedelta(hours=7)
            )


async def test_rate_limit_is_retryable(
    series: AssetReferenceSeries,
    request_message: IntradayRequested,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(429))
    ) as client:
        with pytest.raises(AdapterUnavailableError):
            await IntradayAdapter(client, "https://example.test/chart").fetch(
                request_message, series, START + timedelta(hours=7)
            )


def test_contract_rejects_nan_and_partial_minute() -> None:
    with pytest.raises(ValidationError):
        IntradayBar(start=START, open="NaN", high=100, low=100, close=100)
    with pytest.raises(ValidationError):
        IntradayBar(start=START + timedelta(seconds=1), open=100, high=100, low=100, close=100)


async def test_no_trade_minutes_yet_is_not_terminal(
    series: AssetReferenceSeries,
    request_message: IntradayRequested,
) -> None:
    data = payload()
    del data["chart"]["result"][0]["timestamp"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    ) as client:
        assert (
            await IntradayAdapter(client, "https://example.test/chart").fetch(
                request_message, series, START
            )
            == []
        )
