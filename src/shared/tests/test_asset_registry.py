"""Tests for the executable asset reference registry."""

from __future__ import annotations

import pytest

from shared.reference import (
    REGISTRY_VERSION,
    ROLLOVER_INCLUDE_ALL,
    UnknownAssetError,
    group_of,
    members_of,
    provider_of,
    resolve,
    supported_assets,
)
from shared.schemas.messages import AssetId, PriceKind


def test_supported_assets_cover_every_canonical_asset() -> None:
    # Every AssetId must have an approved reference series (commodities + sector bellwethers).
    assert set(supported_assets()) == set(AssetId)
    assert {AssetId.GOLD, AssetId.BRENT_OIL} <= set(supported_assets())


@pytest.mark.parametrize(
    ("asset_id", "provider_symbol", "expected_exchange"),
    [
        (AssetId.GOLD, "XAUUSD", "COMEX"),
        (AssetId.BRENT_OIL, "UKOIL", "NYMEX"),
    ],
)
def test_resolve_maps_canonical_id_to_frozen_policy(
    asset_id: AssetId, provider_symbol: str, expected_exchange: str
) -> None:
    series = resolve(asset_id)
    assert series.asset_id == asset_id
    assert series.provider_symbol == provider_symbol
    assert series.expected_exchange == expected_exchange
    assert series.provider == "biquote.io"
    assert series.timezone == "America/New_York"
    assert series.currency == "USD"


def test_resolve_preserves_provider_daily_close_semantics() -> None:
    # Policy is provider-independent: raw daily closes, never official settlements, no adjustment.
    for asset_id in supported_assets():
        series = resolve(asset_id)
        assert series.price_kind == PriceKind.PROVIDER_DAILY_CLOSE
        assert series.is_adjusted is False
        assert series.rollover_policy == ROLLOVER_INCLUDE_ALL
        assert series.registry_version == REGISTRY_VERSION


def test_us_assets_keep_their_biquote_mapping() -> None:
    # The refactor must be additive: existing US assets resolve exactly as before.
    for asset_id in (AssetId.LMT_NYSE, AssetId.TSLA_NASDAQ, AssetId.AAPL_NASDAQ):
        series = resolve(asset_id)
        assert series.provider == "biquote.io"
        assert series.currency == "USD"
        assert series.timezone == "America/New_York"
        assert (series.session_completion_hour, series.session_completion_minute) == (17, 0)


def test_non_us_assets_route_to_a_local_provider_and_calendar() -> None:
    # biquote serves no European listing, so EU/Nordic assets must resolve elsewhere with their own
    # currency and session calendar.
    series = resolve("SAAB_B_STO")
    assert series.provider == "yahoo"
    assert series.currency == "SEK"
    assert series.timezone == "Europe/Stockholm"
    assert series.session_completion_hour == 18
    assert series.group_id == "WEAPON_INDUSTRY"


def test_group_membership_resolves_both_ways() -> None:
    assert group_of("SAAB_B_STO") == "WEAPON_INDUSTRY"
    assert "SAAB_B_STO" in members_of("WEAPON_INDUSTRY")


def test_every_asset_belongs_to_exactly_one_group() -> None:
    # Commodities are grouped too, so there is no special case anywhere downstream.
    for asset_id in supported_assets():
        group_id = group_of(asset_id)
        assert group_id
        assert asset_id in members_of(group_id)
    assert group_of(AssetId.GOLD) == "PRECIOUS_METALS"
    assert group_of(AssetId.BRENT_OIL) == "OIL_GAS"


def test_group_lookups_reject_an_unknown_asset() -> None:
    # "no group" must not be confused with "no such asset".
    with pytest.raises(UnknownAssetError):
        group_of("NOT_AN_ASSET")
    assert members_of("NOT_A_GROUP") == ()


def test_provider_of_matches_resolved_series() -> None:
    for asset_id in supported_assets():
        assert provider_of(asset_id) == resolve(asset_id).provider


def test_code_is_documentation_only_and_never_the_provider_symbol() -> None:
    # `code` carries the readable EXCHANGE:TICKER form; biquote rejects that format outright, so it
    # must never be what an adapter sends.
    for asset_id in supported_assets():
        series = resolve(asset_id)
        assert ":" in series.code
        assert series.provider_symbol != series.code


def test_resolve_has_no_validated_fallback_for_the_poc() -> None:
    # No validated fallback provider for the POC: fallback must be None, never a substitution.
    for asset_id in supported_assets():
        assert resolve(asset_id).fallback is None


def test_reference_series_is_immutable() -> None:
    series = resolve(AssetId.GOLD)
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen model raises ValidationError
        series.provider_symbol = "XXX"  # type: ignore[misc]


def test_resolve_unknown_asset_raises() -> None:
    with pytest.raises(UnknownAssetError):
        resolve("UNLISTED")  # type: ignore[arg-type]
