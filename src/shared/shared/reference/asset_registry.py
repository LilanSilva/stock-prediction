"""Executable asset reference registry (source of truth for provider mappings).

Mirrors the P06/T03 frozen policy `poc6-yahoo-reference-v1`
(src/poc/poc6/results/reference-price-policy.json) as typed, versioned constants so every service
resolves a canonical `AssetId` to its approved provider reference series identically.

Business logic uses canonical `AssetId`. Provider symbols such as `GC=F`/`BZ=F` live only here and
inside market-data adapters; they never cross a service boundary as a business identity.

Frozen-policy invariants preserved from P06/T03:
  - Yahoo daily bars are PROVIDER_DAILY_CLOSE, never labelled official exchange settlements.
  - Continuous futures use PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1 (include every positive raw
    close; no adjustment or outcome-based exclusion).
  - No validated fallback exists for the POC: `fallback` is None for both assets.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from shared.reference.exceptions import UnknownAssetError
from shared.schemas.messages import AssetId, PriceKind

REGISTRY_VERSION = "poc6-yahoo-reference-v1"

# The continuous-futures rollover policy pre-declared by P06/T03. Declared before any outcome is
# evaluated, so an observation may be validated against it without look-ahead.
ROLLOVER_INCLUDE_ALL = "PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1"

# Session-completion wall-clock in the provider timezone (17:00 America/New_York per the policy).
SESSION_COMPLETION_HOUR = 17
SESSION_COMPLETION_MINUTE = 0


class AssetReferenceSeries(BaseModel):
    """Approved provider reference series for one canonical asset.

    Immutable. `fallback` is the canonical asset ID of an economically equivalent instrument, or
    None when no fallback has been validated (the POC case). No silent substitution is permitted.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    asset_id: AssetId
    provider: str = Field(min_length=1)
    provider_symbol: str = Field(min_length=1)
    economic_identity: str = Field(min_length=1)
    expected_exchange: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    price_kind: PriceKind
    is_adjusted: bool
    rollover_policy: str = Field(min_length=1)
    fallback: AssetId | None
    registry_version: str = Field(min_length=1)


_REGISTRY: Mapping[AssetId, AssetReferenceSeries] = MappingProxyType(
    {
        AssetId.GOLD: AssetReferenceSeries(
            asset_id=AssetId.GOLD,
            provider="Yahoo Finance chart",
            provider_symbol="GC=F",
            economic_identity="COMEX Gold futures continuous reference",
            expected_exchange="CMX",
            timezone="America/New_York",
            currency="USD",
            price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
            is_adjusted=False,
            rollover_policy=ROLLOVER_INCLUDE_ALL,
            fallback=None,
            registry_version=REGISTRY_VERSION,
        ),
        AssetId.BRENT_OIL: AssetReferenceSeries(
            asset_id=AssetId.BRENT_OIL,
            provider="Yahoo Finance chart",
            provider_symbol="BZ=F",
            economic_identity=(
                "NYMEX Brent Crude Oil Last Day Financial futures continuous reference"
            ),
            expected_exchange="NYM",
            timezone="America/New_York",
            currency="USD",
            price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
            is_adjusted=False,
            rollover_policy=ROLLOVER_INCLUDE_ALL,
            fallback=None,
            registry_version=REGISTRY_VERSION,
        ),
    }
)


def resolve(asset_id: AssetId) -> AssetReferenceSeries:
    """Return the approved reference series for a canonical asset ID.

    Raises UnknownAssetError for any asset without an approved series, so unknown assets are never
    silently treated as valid.
    """
    try:
        return _REGISTRY[asset_id]
    except KeyError as exc:
        raise UnknownAssetError(str(asset_id)) from exc


def supported_assets() -> tuple[AssetId, ...]:
    """Canonical asset IDs that currently have an approved reference series."""
    return tuple(_REGISTRY.keys())
