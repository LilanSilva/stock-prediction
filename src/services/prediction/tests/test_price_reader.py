"""Unit tests for the Scope-B price reader. HTTP is mocked; no live Market Data service."""

from __future__ import annotations

import httpx
from shared.schemas.messages import AssetId

from prediction.config import PredictionSettings
from prediction.price_reader import PriceReader


def _settings() -> PredictionSettings:
    return PredictionSettings(
        market_data_base_url="http://market-data:8000",
        price_lookback_sessions=3,
        price_elevated_threshold_pct=0.01,
    )


def _reader(handler: object, settings: PredictionSettings | None = None) -> PriceReader:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    client = httpx.AsyncClient(transport=transport)
    return PriceReader(settings or _settings(), client=client)


def _closes_payload(asset_id: AssetId, closes: list[str]) -> dict[str, object]:
    return {
        "asset_id": asset_id.value,
        "closes": [{"session": f"2026-07-{10 + i:02d}", "close": c} for i, c in enumerate(closes)],
    }


async def test_is_elevated_true_when_latest_exceeds_baseline_mean_by_threshold() -> None:
    # latest 3400 vs baseline mean of [3300, 3290, 3310] = 3300; 3400 > 3300 * 1.01 = 3333.
    payload = _closes_payload(AssetId.BRENT_OIL, ["3400", "3300", "3290", "3310"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["asset_id"] == "BRENT_OIL"
        assert request.url.params["sessions"] == "4"  # lookback 3 + 1
        return httpx.Response(200, json=payload)

    reader = _reader(handler)
    try:
        assert await reader.is_elevated(AssetId.BRENT_OIL) is True
    finally:
        await reader.close()


async def test_is_elevated_false_when_price_is_flat() -> None:
    # latest 3305 vs baseline mean 3300; 3305 < 3333 threshold -> not elevated.
    payload = _closes_payload(AssetId.BRENT_OIL, ["3305", "3300", "3290", "3310"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    reader = _reader(handler)
    try:
        assert await reader.is_elevated(AssetId.BRENT_OIL) is False
    finally:
        await reader.close()


async def test_is_elevated_false_on_insufficient_data() -> None:
    # A single close has no baseline to compare against -> fail safe.
    payload = _closes_payload(AssetId.GOLD, ["3400"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    reader = _reader(handler)
    try:
        assert await reader.is_elevated(AssetId.GOLD) is False
    finally:
        await reader.close()


async def test_is_elevated_false_on_empty_closes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"asset_id": "GOLD", "closes": []})

    reader = _reader(handler)
    try:
        assert await reader.is_elevated(AssetId.GOLD) is False
    finally:
        await reader.close()


async def test_is_elevated_false_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    reader = _reader(handler)
    try:
        assert await reader.is_elevated(AssetId.GOLD) is False
    finally:
        await reader.close()


async def test_is_elevated_false_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    reader = _reader(handler)
    try:
        assert await reader.is_elevated(AssetId.GOLD) is False
    finally:
        await reader.close()
