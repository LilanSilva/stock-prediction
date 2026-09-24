"""Strict Yahoo minute bars; gaps are omitted and never filled with prices."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError
from shared.reference import AssetReferenceSeries
from shared.schemas.messages import IntradayBar, IntradayRequested

from market_data.adapters.yahoo import _BROWSER_USER_AGENT, YahooAdapter, _epoch_to_utc
from market_data.exceptions import AdapterUnavailableError, InvalidObservationError


class IntradayAdapter:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")

    async def fetch(
        self,
        request: IntradayRequested,
        series: AssetReferenceSeries,
        now: datetime,
    ) -> list[IntradayBar]:
        if series.provider != "yahoo" or series.is_adjusted:
            raise InvalidObservationError("intraday requires unadjusted Yahoo pricing")
        if series.registry_version != request.registry_version:
            raise InvalidObservationError("registry changed during intraday evaluation")
        try:
            response = await self.client.get(
                f"{self.base_url}/{quote(series.provider_symbol, safe='')}",
                params={
                    "interval": "1m",
                    "includePrePost": "false",
                    "events": "splits,div",
                    "period1": str(int(request.opens_at.timestamp())),
                    "period2": str(int(request.closes_at.timestamp())),
                },
                headers={"User-Agent": _BROWSER_USER_AGENT},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdapterUnavailableError("Yahoo intraday request failed") from exc
        try:
            payload: dict[str, Any] = response.json()
            result = YahooAdapter._require_result(series, payload)
            meta = result["meta"]
            # Unlike the legacy daily parser, missing identity is insufficient evidence here.
            if not all(key in meta for key in ("symbol", "currency", "exchangeTimezoneName")):
                raise InvalidObservationError("missing intraday listing identity")
            YahooAdapter._validate_identity(series, meta)
            if meta["symbol"] != series.provider_symbol:
                raise InvalidObservationError("intraday listing symbol mismatch")
            if result.get("events", {}).get("splits"):
                raise InvalidObservationError("split during intraday session")
            timestamps = result.get("timestamp")
            if timestamps is None or timestamps == []:
                return []  # A valid listing may have no completed trade minute yet.
            if not isinstance(timestamps, list):
                raise InvalidObservationError("invalid intraday timestamp array")
            prices = result["indicators"]["quote"][0]
            if any(len(prices[k]) != len(timestamps) for k in ("open", "high", "low", "close")):
                raise InvalidObservationError("intraday OHLC length mismatch")
            bars: dict[datetime, IntradayBar] = {}
            for i, stamp in enumerate(timestamps):
                start = _epoch_to_utc(stamp)
                if not request.opens_at <= start < request.closes_at:
                    continue
                if start + timedelta(minutes=1) > now:
                    continue
                values = {key: prices[key][i] for key in ("open", "high", "low", "close")}
                if any(value is None for value in values.values()):
                    continue
                bar = IntradayBar(start=start, **values)
                if start in bars and bars[start] != bar:
                    raise InvalidObservationError("conflicting duplicate intraday timestamp")
                bars[start] = bar
            return sorted(bars.values(), key=lambda b: b.start)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise InvalidObservationError("malformed intraday chart") from exc
