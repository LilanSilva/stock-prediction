"""Tests for the executable asset reference registry."""

from __future__ import annotations

import pytest

from shared.reference import (
    REGISTRY_VERSION,
    ROLLOVER_INCLUDE_ALL,
    UnknownAssetError,
    resolve,
    supported_assets,
)
from shared.schemas.messages import AssetId, PriceKind


def test_supported_assets_are_the_poc_pair() -> None:
    assert set(supported_assets()) == {AssetId.GOLD, AssetId.BRENT_OIL}


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
    # POC-7 policy: raw biquote daily closes, never official settlements, no adjustment.
    for asset_id in supported_assets():
        series = resolve(asset_id)
        assert series.price_kind == PriceKind.PROVIDER_DAILY_CLOSE
        assert series.is_adjusted is False
        assert series.rollover_policy == ROLLOVER_INCLUDE_ALL
        assert series.registry_version == REGISTRY_VERSION


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
