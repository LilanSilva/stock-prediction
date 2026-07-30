"""Async biquote.io OHLC adapter (POC-7 provider; replaces the retired Yahoo chart adapter).

Validated by src/poc/poc7-biquote-market-data. Keeps the same public surface the processor depends
on (`get_close`, `fetch_observations`, and the PriceNotYetAvailable / InvalidObservation /
AdapterUnavailable exception contract) so the rest of the service is unchanged.

biquote differences from Yahoo that this adapter handles:
  - Response shape is ``{"symbol", "interval", "bars": [...]}`` with no ``meta`` block, so there is
    no currency/exchange/timezone metadata to validate; identity is trusted from the symbol mapping.
  - Each daily bar is stamped ``openTime`` at UTC midnight (``2026-07-30T00:00:00Z``); the session
    date IS that calendar date (no intraday-to-session shift as with Yahoo).
  - The current, still-forming bar is flagged ``isOpen: true``; it is a last-tick value, not a
    settled close, so it is excluded (only ``isOpen == false`` bars become observations).

Behaviour preserved: include-all rollover (every positive finite close kept), immutable
CloseObservation values labelled PROVIDER_DAILY_CLOSE, session-not-available signalled as
PriceNotYetAvailableError, malformed provider data as InvalidObservationError (terminal), transport
failures as AdapterUnavailableError (transient).
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

_SOURCE = "biquote.io"
_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


class BiquoteAdapter:
    """Fetches approved reference closes for a canonical asset from the biquote.io OHLC API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str = "https://biquote.io/api",
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
            f"no provider bar for {asset_id} session {session.isoformat()}"
        )

    async def fetch_observations(
        self, asset_id: AssetId, session: date
    ) -> list[CloseObservation]:
        """Fetch and validate all settled reference closes in a window bracketing `session`."""
        series = resolve(asset_id)
        start = datetime.combine(
            session - timedelta(days=self._fetch_window_days), datetime.min.time(), tzinfo=UTC
        )
        end = datetime.combine(
            session + timedelta(days=self._fetch_window_days), datetime.min.time(), tzinfo=UTC
        )
        payload = await self._request_ohlc(series, start, end)
        return self._parse(series, payload)

    async def _request_ohlc(
        self, series: AssetReferenceSeries, start: datetime, end: datetime
    ) -> dict[str, object]:
        encoded = urllib.parse.quote(series.provider_symbol, safe="")
        url = f"{self._base_url}/{encoded}/ohlc"
        params = {
            "interval": "1d",
            "from": start.strftime(_ISO_Z),
            "to": end.strftime(_ISO_Z),
        }
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data: dict[str, object] = response.json()
            return data
        except httpx.HTTPError as exc:
            raise AdapterUnavailableError(
                f"biquote request failed for {series.asset_id}: {exc}"
            ) from exc

    def _parse(
        self, series: AssetReferenceSeries, payload: dict[str, object]
    ) -> list[CloseObservation]:
        if series.rollover_policy != ROLLOVER_INCLUDE_ALL:  # pragma: no cover - single POC policy
            raise InvalidObservationError(
                f"unsupported rollover policy: {series.rollover_policy}"
            )
        bars = payload.get("bars")
        if not isinstance(bars, list):
            raise InvalidObservationError("provider bars block is missing or malformed")

        fetched_at = datetime.now(UTC)
        observations: list[CloseObservation] = []
        for bar in bars:
            if not isinstance(bar, dict):
                raise InvalidObservationError("provider bar is not an object")
            # Skip the still-forming day: its close is a last tick, not a settled daily close.
            if bar.get("isOpen") is True:
                continue
            close_value = _valid_close(bar.get("close"))
            if close_value is None:
                # include-all rollover keeps every positive finite close; skip provider nulls/gaps.
                continue
            provider_bar_time = _parse_open_time(bar.get("openTime"))
            observations.append(
                CloseObservation(
                    session=provider_bar_time.date(),
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
        return observations


def _parse_open_time(raw: object) -> datetime:
    if not isinstance(raw, str):
        raise InvalidObservationError("provider bar openTime is missing or malformed")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidObservationError(f"provider bar openTime is unparseable: {raw!r}") from exc
    return parsed.astimezone(UTC)


def _valid_close(raw_close: object) -> Decimal | None:
    if raw_close is None or isinstance(raw_close, bool):
        return None
    try:
        as_float = float(raw_close)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(as_float) or as_float <= 0:
        return None
    return Decimal(str(as_float))
