"""Async client for the Market Data recent-prices endpoint (Scope-B price gate).

The gate answers one question: is an asset's price currently elevated versus recent sessions? A
RESOLUTION event ("war called off") should only push an asset DOWN when there is an inflated risk
premium to unwind. ``is_elevated`` is deliberately fail-safe: any missing/insufficient data or
transport error returns ``False`` so a de-escalation is never forced DOWN without confirmed
elevation.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from statistics import mean

import httpx
import structlog
from shared.schemas.messages import AssetId

from prediction.config import PredictionSettings

logger = structlog.get_logger(__name__)


class PriceReader:
    """Reads recent reference closes from the Market Data service to gauge elevated risk premium."""

    def __init__(
        self, settings: PredictionSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url = settings.market_data_base_url.rstrip("/")
        self._lookback = settings.price_lookback_sessions
        self._threshold = Decimal(str(settings.price_elevated_threshold_pct))
        # A caller-supplied client is owned by the caller; an internally created one we close.
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.market_data_timeout_seconds)

    async def is_elevated(self, asset_id: AssetId) -> bool:
        """Return whether the latest close exceeds the recent baseline mean by the threshold."""
        closes = await self._recent_closes(asset_id)
        # Need the latest plus at least one prior session to form a baseline; otherwise fail safe.
        if len(closes) < 2:
            return False
        latest = closes[0]
        baseline_mean = mean(closes[1:])
        if baseline_mean <= 0:
            return False
        return latest > baseline_mean * (Decimal(1) + self._threshold)

    async def is_price_available(self, asset_id: AssetId) -> bool:
        """Return whether Market Data currently exposes at least one recent close for the asset.

        Used to decide the market-open vs collapse path: no reachable price (weekend/holiday with no
        stored close, or a transport failure) selects the closed path.
        """
        return len(await self._recent_closes(asset_id)) >= 1

    async def _recent_closes(self, asset_id: AssetId) -> list[Decimal]:
        params: dict[str, str | int] = {
            "asset_id": asset_id.value,
            "sessions": self._lookback + 1,
        }
        try:
            response = await self._client.get(f"{self._base_url}/prices/recent", params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            # Never raise into the close sweep: an unreachable Market Data service must not force a
            # DOWN nor abort the prediction batch.
            logger.warning("price_reader_unavailable", asset_id=asset_id.value, error=str(exc))
            return []
        return self._parse_closes(payload)

    @staticmethod
    def _parse_closes(payload: object) -> list[Decimal]:
        if not isinstance(payload, dict):
            return []
        raw = payload.get("closes")
        if not isinstance(raw, list):
            return []
        closes: list[Decimal] = []
        for item in raw:
            if not isinstance(item, dict):
                return []
            try:
                closes.append(Decimal(str(item["close"])))
            except (KeyError, InvalidOperation):
                return []
        return closes

    async def close(self) -> None:
        """Close the underlying HTTP client when this reader owns it (graceful shutdown)."""
        if self._owns_client:
            await self._client.aclose()
