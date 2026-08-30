"""Tests for per-asset provider routing.

The registry decides which vendor prices each asset via the ``provider`` field. Routing follows that
field, never the exchange, so a market can be re-pointed at another vendor by a registry edit alone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

import pytest
from shared.reference import provider_of, resolve, supported_assets
from shared.schemas.messages import AssetId, CloseObservation, PriceKind

from market_data.adapters import router as router_module
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


def test_us_assets_route_to_yahoo() -> None:
    router, _, yahoo = _router()
    assert router.adapter_for(AssetId.XOM_NYSE) is yahoo
    assert router.adapter_for(AssetId.LMT_NYSE) is yahoo


def test_non_us_assets_route_to_yahoo() -> None:
    router, _, yahoo = _router()
    for asset_id in (AssetId.SAAB_B_STO, AssetId.NOVO_B_CPH, AssetId.ASML_AMS, AssetId.SAP_ETR):
        assert router.adapter_for(asset_id) is yahoo


def test_routing_follows_the_provider_field_not_the_exchange() -> None:
    router, _, yahoo = _router()
    for asset_id in (AssetId.UAL_NASDAQ, AssetId.MRNA_NASDAQ, AssetId.ZM_NASDAQ):
        assert router.adapter_for(asset_id) is yahoo


async def test_get_close_delegates_to_the_routed_adapter() -> None:
    router, _, yahoo = _router()
    await router.get_close(AssetId.SAAB_B_STO, date(2026, 7, 29))
    await router.get_close(AssetId.XOM_NYSE, date(2026, 7, 29))
    assert yahoo.calls == [AssetId.SAAB_B_STO, AssetId.XOM_NYSE]


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


class _FailingAdapter:
    """Primary adapter that always raises PriceNotYetAvailableError."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[AssetId] = []

    async def get_close(self, asset_id: AssetId, session: date) -> CloseObservation:
        self.calls.append(asset_id)
        from market_data.exceptions import PriceNotYetAvailableError
        raise PriceNotYetAvailableError(f"no provider bar for {asset_id} session {session}")

    async def fetch_observations(
        self, asset_id: AssetId, session: date
    ) -> list[CloseObservation]:
        return []


class _FakeSeries:
    """Minimal stand-in for the registry entry the router reads (provider + fallback only)."""

    def __init__(self, provider: str, fallback: AssetId | None) -> None:
        self.provider = provider
        self.fallback = fallback


async def test_fallback_used_when_primary_missing_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    # A registry entry may name a fallback asset; the router retries there when the primary
    # provider has no bar for the session.
    primary = _FailingAdapter("primary")
    backup = _StubAdapter("backup")
    router = AdapterRouter({"primary": primary, "backup": backup})

    series = {
        AssetId.LMT_NYSE: _FakeSeries("primary", AssetId.NEM_NYSE),
        AssetId.NEM_NYSE: _FakeSeries("backup", None),
    }
    monkeypatch.setattr(router_module, "resolve", lambda asset_id: series[asset_id])

    obs = await router.get_close(AssetId.LMT_NYSE, date(2026, 8, 4))

    assert obs.source == "backup"
    assert primary.calls == [AssetId.LMT_NYSE]
    assert backup.calls == [AssetId.NEM_NYSE]


async def test_no_fallback_re_raises() -> None:
    # Assets without a fallback declared still propagate PriceNotYetAvailableError normally.
    from market_data.exceptions import PriceNotYetAvailableError

    failing_yahoo = _FailingAdapter("yahoo")
    router = AdapterRouter({"yahoo": failing_yahoo})

    with pytest.raises(PriceNotYetAvailableError):
        await router.get_close(AssetId.XOM_NYSE, date(2026, 8, 4))
