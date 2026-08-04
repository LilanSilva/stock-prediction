"""Per-asset provider routing.

One asset maps to exactly one provider, declared as ``provider`` in the asset registry, because no
single vendor covers every market: biquote.io serves a curated list of US mega-caps, Yahoo serves
Stockholm/Euronext/Xetra. Routing here means adding a market is a registry edit plus (if the vendor
is new) one adapter, with no change to the request pipeline.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

import structlog
from shared.reference import UnknownAssetError, resolve
from shared.schemas.messages import AssetId, CloseObservation

from market_data.exceptions import InvalidObservationError

logger = structlog.get_logger(__name__)


class PriceAdapter(Protocol):
    """The surface the request processor depends on; both provider adapters satisfy it."""

    async def get_close(self, asset_id: AssetId, session: date) -> CloseObservation: ...

    async def fetch_observations(
        self, asset_id: AssetId, session: date
    ) -> list[CloseObservation]: ...


class AdapterRouter:
    """Dispatches each request to the adapter that serves that asset's provider."""

    def __init__(self, adapters: dict[str, PriceAdapter]) -> None:
        self._adapters = dict(adapters)

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def adapter_for(self, asset_id: AssetId) -> PriceAdapter:
        """The adapter serving ``asset_id``.

        An unknown asset or an unconfigured provider is terminal data, not a transient
        failure: the request is dead-lettered rather than retried forever against a provider
        that cannot serve it.
        """
        try:
            provider = resolve(asset_id).provider
        except UnknownAssetError as exc:
            raise InvalidObservationError(f"unknown asset {asset_id}: {exc}") from exc
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise InvalidObservationError(
                f"no adapter configured for provider {provider!r} (asset {asset_id}); "
                f"configured: {', '.join(self.providers) or 'none'}"
            )
        return adapter

    async def get_close(self, asset_id: AssetId, session: date) -> CloseObservation:
        return await self.adapter_for(asset_id).get_close(asset_id, session)

    async def fetch_observations(
        self, asset_id: AssetId, session: date
    ) -> list[CloseObservation]:
        return await self.adapter_for(asset_id).fetch_observations(asset_id, session)
