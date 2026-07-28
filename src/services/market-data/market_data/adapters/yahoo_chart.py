"""Async Yahoo Finance chart-JSON adapter.

Ports the P06/T03-approved fetch and validation logic (src/poc/poc6/market_policy.py) to an async
httpx client. Behaviour preserved verbatim so the frozen `poc6-yahoo-reference-v1` policy still
holds:

  - Accepts a canonical `AssetId`; resolves the provider symbol/exchange/timezone/currency from the
    shared asset registry. Provider symbols never leave this adapter as a business identity.
  - Validates the provider `meta` (currency, exchange, timezone) against the registry.
  - Applies the include-all continuous-futures rollover policy: every positive finite raw close is
    kept; no adjustment, no outcome-based exclusion.
  - Returns immutable `CloseObservation` values (PROVIDER_DAILY_CLOSE, never official settlement).

Session-not-yet-available is signalled with PriceNotYetAvailableError so the caller keeps the
request pending; malformed/mismatched provider data raises InvalidObservationError (terminal).
"""

from __future__ import annotations

import math
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
from shared.reference import AssetReferenceSeries, resolve
from shared.reference.asset_registry import ROLLOVER_INCLUDE_ALL
from shared.schemas.messages import AssetId, CloseObservation

from market_data.exceptions import (
    AdapterUnavailableError,
    InvalidObservationError,
    PriceNotYetAvailableError,
)
from market_data.sessions import provider_bar_to_session

_SOURCE = "Yahoo Finance chart"


class YahooChartAdapter:
    """Fetches approved reference closes for a canonical asset from the Yahoo chart JSON API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str = "https://query1.finance.yahoo.com/v8/finance/chart",
        fetch_window_days: int = 10,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._fetch_window_days = fetch_window_days

    async def get_close(self, asset_id: AssetId, session: date) -> CloseObservation:
        """Return the validated reference close for `asset_id` on the given local session.

        Raises:
            PriceNotYetAvailableError: the session has no bar yet in the returned series.
            InvalidObservationError: provider metadata or the bar failed terminal validation.
            AdapterUnavailableError: the provider could not be reached / returned a transient error.
        """
        observations = await self.fetch_observations(asset_id, session)
        for observation in observations:
            if observation.session == session:
                return observation
        raise PriceNotYetAvailableError(
            f"no provider bar for {asset_id} session {session.isoformat()}"
        )

    async def fetch_observations(
        self, asset_id: AssetId, session: date
    ) -> list[CloseObservation]:
        """Fetch and validate all reference closes in a window bracketing `session`."""
        series = resolve(asset_id)
        start = datetime.combine(
            session - timedelta(days=self._fetch_window_days), datetime.min.time(), tzinfo=UTC
        )
        end = datetime.combine(
            session + timedelta(days=self._fetch_window_days), datetime.min.time(), tzinfo=UTC
        )
        payload = await self._request_chart(series, start, end)
        return self._parse(series, payload)

    async def _request_chart(
        self, series: AssetReferenceSeries, start: datetime, end: datetime
    ) -> dict[str, object]:
        encoded = urllib.parse.quote(series.provider_symbol, safe="")
        url = f"{self._base_url}/{encoded}"
        params = {
            "period1": str(int(start.timestamp())),
            "period2": str(int(end.timestamp())),
            "interval": "1d",
            "events": "history",
        }
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data: dict[str, object] = response.json()
            return data
        except httpx.HTTPError as exc:
            raise AdapterUnavailableError(
                f"Yahoo chart request failed for {series.asset_id}: {exc}"
            ) from exc

    def _parse(
        self, series: AssetReferenceSeries, payload: dict[str, object]
    ) -> list[CloseObservation]:
        result = _first_result(payload)
        metadata = _require_mapping(result.get("meta"), "meta")
        _validate_metadata(series, metadata)

        timestamps = result.get("timestamp") or []
        quote_block = result.get("indicators")
        closes = _extract_closes(quote_block)
        if not isinstance(timestamps, list):
            raise InvalidObservationError("provider timestamp block is not a list")

        fetched_at = datetime.now(UTC)
        observations: list[CloseObservation] = []
        for raw_ts, raw_close in zip(timestamps, closes, strict=False):
            close_value = _valid_close(raw_close)
            if close_value is None:
                # include-all rollover keeps every positive finite close; skip provider nulls/gaps.
                continue
            provider_bar_time = datetime.fromtimestamp(int(raw_ts), tz=UTC)
            session = provider_bar_to_session(provider_bar_time, series.timezone)
            observations.append(
                CloseObservation(
                    session=session,
                    close=close_value,
                    provider_bar_time=provider_bar_time,
                    fetched_at=fetched_at,
                    source=_SOURCE,
                    provider_symbol=series.provider_symbol,
                    price_kind=series.price_kind,
                    is_adjusted=series.is_adjusted,
                    registry_version=series.registry_version,
                )
            )
        if series.rollover_policy != ROLLOVER_INCLUDE_ALL:  # pragma: no cover - single POC policy
            raise InvalidObservationError(
                f"unsupported rollover policy: {series.rollover_policy}"
            )
        return observations


def _first_result(payload: dict[str, object]) -> dict[str, object]:
    chart = _require_mapping(payload.get("chart"), "chart")
    error = chart.get("error")
    if error is not None:
        raise InvalidObservationError(f"provider returned chart error: {error}")
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise InvalidObservationError("provider chart result is empty")
    return _require_mapping(results[0], "chart.result[0]")


def _validate_metadata(series: AssetReferenceSeries, metadata: dict[str, object]) -> None:
    if metadata.get("currency") != series.currency:
        raise InvalidObservationError(
            f"unexpected currency for {series.asset_id}: {metadata.get('currency')!r}"
        )
    if metadata.get("exchangeName") != series.expected_exchange:
        raise InvalidObservationError(
            f"unexpected exchange for {series.asset_id}: {metadata.get('exchangeName')!r}"
        )
    if metadata.get("exchangeTimezoneName") != series.timezone:
        raise InvalidObservationError(
            f"unexpected timezone for {series.asset_id}: {metadata.get('exchangeTimezoneName')!r}"
        )


def _extract_closes(quote_block: object) -> list[object]:
    if not isinstance(quote_block, dict):
        raise InvalidObservationError("provider indicators block is missing")
    quotes = quote_block.get("quote")
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        raise InvalidObservationError("provider quote block is missing")
    closes = quotes[0].get("close")
    if not isinstance(closes, list):
        raise InvalidObservationError("provider close series is missing")
    return closes


def _valid_close(raw_close: object) -> Decimal | None:
    if raw_close is None:
        return None
    try:
        as_float = float(raw_close)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(as_float) or as_float <= 0:
        return None
    return Decimal(str(as_float))


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InvalidObservationError(f"provider {label} is missing or malformed")
    return value
