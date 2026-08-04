"""Async Yahoo Finance chart adapter for non-US listings (Stockholm, Copenhagen, Euronext, Xetra).

Exists because biquote.io serves a curated list of US mega-caps only: probed 2026-08-03, every
European listing returns 0 bars, including EU giants that trade on US exchanges (ASML, SAP, NVO,
SHEL). Yahoo covers Stockholm and Euronext, so non-US assets route here while US assets stay on
biquote (see the `provider` field in the asset registry).

**The User-Agent is load-bearing.** Yahoo blocks non-browser agents with HTTP 429. The 429s that
retired this endpoint in POC-7 were caused by its custom agent
(``feed-analyzer-poc6/1.0 (local research POC)``), not by IP rate limiting: 40 concurrent requests
with a browser agent all return 200, while 8 with curl's default agent all return 429. Do not
"clean up" ``_BROWSER_USER_AGENT`` into a service-identifying string.

Yahoo differences from biquote that this adapter handles:
  - Bars are **parallel arrays** (``timestamp[]`` alongside ``indicators.quote[0].close[]``) rather
    than a list of bar objects.
  - Gaps appear as ``null`` entries *inside* the close array. These are skipped, never zero-filled:
    treating a null as a price would corrupt the close-to-close return.
  - There is **no ``isOpen`` flag**. The still-forming bar is excluded by checking the session
    against the market's own closing clock (``shared.calendar``), so an unsettled last tick is never
    stored as a daily close.
  - Timestamps are epoch seconds that must be mapped to the *local market* session date. Getting
    this wrong shifts every European close by a day, which produces plausible but wrong scores.
  - ``meta`` carries ``currency`` and ``exchangeTimezoneName``, which are validated against the
    registry so a provider returning a different listing is caught rather than trusted.

Behaviour preserved from the biquote adapter: include-all rollover (every positive finite close
kept), immutable CloseObservation values labelled PROVIDER_DAILY_CLOSE, session-not-available as
PriceNotYetAvailableError, malformed provider data as InvalidObservationError (terminal), transport
failures as AdapterUnavailableError (transient).
"""

from __future__ import annotations

import math
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import structlog
from shared.calendar import is_session_complete, provider_bar_to_session
from shared.calendar.exceptions import UnsupportedTimezoneError
from shared.reference import AssetReferenceSeries, resolve
from shared.reference.asset_registry import ROLLOVER_INCLUDE_ALL
from shared.schemas.messages import AssetId, CloseObservation, PriceKind

from market_data.exceptions import (
    AdapterUnavailableError,
    InvalidObservationError,
    PriceNotYetAvailableError,
)

logger = structlog.get_logger(__name__)

_SOURCE = "yahoo"

# See the module docstring: a browser User-Agent is the difference between HTTP 200 and HTTP 429.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class YahooAdapter:
    """Fetches approved reference closes for a canonical asset from the Yahoo chart API."""

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
        """Return the validated reference close for `asset_id` on the given session date.

        Raises:
            PriceNotYetAvailableError: no settled bar for that session in the returned window.
            InvalidObservationError: provider payload failed terminal validation.
            AdapterUnavailableError: the provider could not be reached / returned a transient error.
        """
        observations = await self.fetch_observations(asset_id, session)
        for observation in observations:
            if observation.session == session:
                return observation
        raise PriceNotYetAvailableError(
            f"no settled Yahoo bar for {asset_id} session {session.isoformat()}"
        )

    async def fetch_observations(
        self, asset_id: AssetId, session: date
    ) -> list[CloseObservation]:
        """Fetch and validate all settled reference closes in a window bracketing `session`."""
        series = resolve(asset_id)
        payload = await self._request_chart(series, session)
        return self._parse(series, payload)

    async def _request_chart(
        self, series: AssetReferenceSeries, session: date
    ) -> dict[str, Any]:
        encoded = urllib.parse.quote(series.provider_symbol, safe="")
        start = datetime.combine(
            session - timedelta(days=self._fetch_window_days), datetime.min.time(), tzinfo=UTC
        )
        end = datetime.combine(
            session + timedelta(days=self._fetch_window_days), datetime.min.time(), tzinfo=UTC
        )
        params = {
            "interval": "1d",
            "period1": str(int(start.timestamp())),
            "period2": str(int(end.timestamp())),
        }
        try:
            response = await self._client.get(
                f"{self._base_url}/{encoded}",
                params=params,
                headers={"User-Agent": _BROWSER_USER_AGENT},
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data
        except httpx.HTTPError as exc:
            # Includes 429: transient by contract, so the scheduler retries with backoff rather than
            # dead-lettering a request that would succeed later.
            raise AdapterUnavailableError(
                f"yahoo request failed for {series.asset_id}: {exc}"
            ) from exc

    def _parse(
        self, series: AssetReferenceSeries, payload: dict[str, object]
    ) -> list[CloseObservation]:
        if series.rollover_policy != ROLLOVER_INCLUDE_ALL:  # pragma: no cover - single POC policy
            raise InvalidObservationError(
                f"unsupported rollover policy: {series.rollover_policy}"
            )

        result = self._require_result(series, payload)
        meta = result.get("meta")
        if not isinstance(meta, dict):
            raise InvalidObservationError("yahoo meta block is missing or malformed")
        self._validate_identity(series, meta)

        timestamps = result.get("timestamp")
        if not isinstance(timestamps, list):
            raise InvalidObservationError("yahoo timestamp array is missing or malformed")
        closes = self._close_array(result)
        if len(closes) != len(timestamps):
            raise InvalidObservationError(
                f"yahoo timestamp/close length mismatch: {len(timestamps)} vs {len(closes)}"
            )

        now = datetime.now(UTC)
        fetched_at = now
        observations: list[CloseObservation] = []
        for raw_time, raw_close in zip(timestamps, closes, strict=True):
            close_value = _valid_close(raw_close)
            if close_value is None:
                # include-all rollover keeps every positive finite close; a null is a provider gap
                # (no trade), never a zero price.
                continue
            bar_time = _epoch_to_utc(raw_time)
            bar_session = self._session_of(series, bar_time)
            # Yahoo has no isOpen flag, so the still-forming bar is excluded by the market's own
            # closing clock. Storing it would record a last tick as a settled daily close.
            if not is_session_complete(
                bar_session,
                series.timezone,
                now=now,
                hour=series.session_completion_hour,
                minute=series.session_completion_minute,
            ):
                continue
            observations.append(
                CloseObservation(
                    session=bar_session,
                    close=close_value,
                    provider_bar_time=bar_time,
                    fetched_at=fetched_at,
                    source=_SOURCE,
                    provider_symbol=series.provider_symbol,
                    price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
                    is_adjusted=series.is_adjusted,
                    registry_version=series.registry_version,
                )
            )
        return observations

    @staticmethod
    def _require_result(
        series: AssetReferenceSeries, payload: dict[str, object]
    ) -> dict[str, Any]:
        chart = payload.get("chart")
        if not isinstance(chart, dict):
            raise InvalidObservationError("yahoo chart block is missing or malformed")
        error = chart.get("error")
        if error is not None:
            # Yahoo reports an unknown symbol in-band with HTTP 200; that is terminal registry data.
            raise InvalidObservationError(
                f"yahoo rejected {series.provider_symbol!r}: {error}"
            )
        result = chart.get("result")
        if not isinstance(result, list) or not result:
            raise PriceNotYetAvailableError(
                f"yahoo returned no series for {series.provider_symbol}"
            )
        first = result[0]
        if not isinstance(first, dict):
            raise InvalidObservationError("yahoo result entry is not an object")
        return first

    @staticmethod
    def _close_array(result: dict[str, Any]) -> list[object]:
        indicators = result.get("indicators")
        if not isinstance(indicators, dict):
            raise InvalidObservationError("yahoo indicators block is missing or malformed")
        quotes = indicators.get("quote")
        if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
            raise InvalidObservationError("yahoo quote block is missing or malformed")
        # Raw (unadjusted) closes only: `adjclose` is back-adjusted for splits/dividends and would
        # silently change historical values between fetches, breaking immutable observations.
        closes = quotes[0].get("close")
        if not isinstance(closes, list):
            raise InvalidObservationError("yahoo close array is missing or malformed")
        return closes

    @staticmethod
    def _validate_identity(series: AssetReferenceSeries, meta: dict[str, Any]) -> None:
        """Reject a payload whose identity contradicts the registry (wrong listing for a symbol)."""
        currency = meta.get("currency")
        if currency is not None and str(currency).upper() != series.currency.upper():
            raise InvalidObservationError(
                f"{series.asset_id}: provider currency {currency!r} != registry "
                f"{series.currency!r}"
            )
        timezone_name = meta.get("exchangeTimezoneName")
        if timezone_name is not None and str(timezone_name) != series.timezone:
            raise InvalidObservationError(
                f"{series.asset_id}: provider timezone {timezone_name!r} != registry "
                f"{series.timezone!r}"
            )

    @staticmethod
    def _session_of(series: AssetReferenceSeries, bar_time: datetime) -> date:
        try:
            return provider_bar_to_session(bar_time, series.timezone)
        except UnsupportedTimezoneError as exc:
            raise InvalidObservationError(str(exc)) from exc


def _epoch_to_utc(raw: object) -> datetime:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise InvalidObservationError(f"yahoo bar timestamp is not a number: {raw!r}")
    try:
        return datetime.fromtimestamp(float(raw), UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise InvalidObservationError(f"yahoo bar timestamp is out of range: {raw!r}") from exc


def _valid_close(raw_close: object) -> Decimal | None:
    if raw_close is None or isinstance(raw_close, bool):
        return None
    if not isinstance(raw_close, int | float | str):
        return None
    try:
        as_float = float(raw_close)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(as_float) or as_float <= 0:
        return None
    try:
        return Decimal(str(as_float))
    except InvalidOperation:  # pragma: no cover - guarded by the float() conversion above
        return None
