"""Tests for per-asset provider routing.

The registry decides which vendor prices each asset, because no single provider covers every market:
biquote serves a curated set of US mega-caps, Yahoo serves Stockholm/Euronext/Xetra.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

import pytest
from shared.reference import provider_of, resolve, supported_assets
from shared.schemas.messages import AssetId, CloseObservation, PriceKind

from market_data.adapters.router import AdapterRouter
from market_data.exceptions import InvalidObservationError


class _StubAdapter:
    """Records which asset it was asked for so routing can be asserted."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[AssetId] = []

    async def get_close(self, asset_id: AssetId, session: date) -> CloseObservation:
        self.calls.append(asset_id)
        return CloseObservation(
            session=session,
            close=Decimal("1.0"),
            provider_bar_time=datetime.combine(session, datetime.min.time(), tzinfo=UTC),
            fetched_at=datetime.now(UTC),
            source=self.name,
            provider_symbol="STUB",
            price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
            is_adjusted=False,
            registry_version="test",
        )

    async def fetch_observations(
        self, asset_id: AssetId, session: date
    ) -> list[CloseObservation]:
        return [await self.get_close(asset_id, session)]


def _router() -> tuple[AdapterRouter, _StubAdapter, _StubAdapter]:
    biquote = _StubAdapter("biquote.io")
    yahoo = _StubAdapter("yahoo")
    return AdapterRouter({"biquote.io": biquote, "yahoo": yahoo}), biquote, yahoo


def test_us_assets_route_to_biquote() -> None:
    router, biquote, _ = _router()
    assert router.adapter_for(AssetId.GOLD) is biquote
    assert router.adapter_for(AssetId.LMT_NYSE) is biquote


def test_non_us_assets_route_to_yahoo() -> None:
    # biquote returns 0 bars for every European listing, so these must not reach it.
    router, _, yahoo = _router()
    for asset_id in (AssetId.SAAB_B_STO, AssetId.NOVO_B_CPH, AssetId.ASML_AMS, AssetId.SAP_ETR):
        assert router.adapter_for(asset_id) is yahoo


def test_us_listings_biquote_cannot_serve_route_to_yahoo() -> None:
    # Probed 2026-08-03: biquote returns 0 bars for these despite them being US-listed, so the
    # registry routes them to Yahoo. Routing follows the provider field, not the exchange.
    router, _, yahoo = _router()
    for asset_id in (AssetId.BNTX_NASDAQ, AssetId.UAL_NASDAQ, AssetId.VLO_NYSE):
        assert router.adapter_for(asset_id) is yahoo


async def test_get_close_delegates_to_the_routed_adapter() -> None:
    router, biquote, yahoo = _router()
    await router.get_close(AssetId.SAAB_B_STO, date(2026, 7, 29))
    await router.get_close(AssetId.GOLD, date(2026, 7, 29))
    assert yahoo.calls == [AssetId.SAAB_B_STO]
    assert biquote.calls == [AssetId.GOLD]


async def test_fetch_observations_delegates_to_the_routed_adapter() -> None:
    router, _, yahoo = _router()
    observations = await router.fetch_observations(AssetId.AZN_STO, date(2026, 7, 29))
    assert yahoo.calls == [AssetId.AZN_STO]
    assert [o.source for o in observations] == ["yahoo"]


def test_unknown_asset_is_terminal() -> None:
    # No registry entry means no provider; dead-letter rather than retry forever.
    router, _, _ = _router()
    with pytest.raises(InvalidObservationError, match="unknown asset"):
        router.adapter_for(cast(AssetId, "NOT_IN_REGISTRY"))


def test_unconfigured_provider_is_terminal() -> None:
    # A registry entry naming a vendor with no adapter is a deployment error, not a transient one.
    router = AdapterRouter({"biquote.io": _StubAdapter("biquote.io")})
    with pytest.raises(InvalidObservationError, match="no adapter configured"):
        router.adapter_for(AssetId.SAAB_B_STO)


def test_every_registered_asset_is_routable() -> None:
    # Guards against adding an asset whose provider has no adapter: it would otherwise fail only
    # when that asset first needed a price.
    router, _, _ = _router()
    for asset_id in supported_assets():
        adapter = router.adapter_for(AssetId(asset_id))
        assert adapter.name == provider_of(asset_id) == resolve(asset_id).provider
