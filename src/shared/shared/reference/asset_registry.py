"""Executable asset reference registry (source of truth for provider mappings).

Reflects the POC-7 provider migration to biquote.io (`biquote-reference-v1`;
src/poc/poc7-biquote-market-data/) as typed, versioned constants so every service resolves a
canonical `AssetId` to its approved provider reference series identically. This replaces the earlier
`poc6-yahoo-reference-v1` (Yahoo Finance chart `GC=F`/`BZ=F`), which was retired because the Yahoo
endpoint rate-limits this host's IP (HTTP 429). See backlog/POC/poc-7-biquote-price-source.md.

Business logic uses canonical `AssetId`. Provider symbols such as `XAUUSD`/`UKOIL` live only here
and inside market-data adapters; they never cross a service boundary as a business identity.

Policy invariants (carried over from P06/T03, provider swapped):
  - biquote daily bars are PROVIDER_DAILY_CLOSE, never labelled official exchange settlements.
  - Continuous series use PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1 (include every positive raw
    close; no adjustment or outcome-based exclusion).
  - No validated fallback exists for the POC: `fallback` is None for both assets.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from shared.reference.exceptions import UnknownAssetError
from shared.schemas.messages import AssetId, PriceKind

REGISTRY_VERSION = "biquote-reference-v1"

# The continuous-futures rollover policy pre-declared by P06/T03. Declared before any outcome is
# evaluated, so an observation may be validated against it without look-ahead.
ROLLOVER_INCLUDE_ALL = "PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1"

# Session-completion wall-clock for the market calendar (17:00 America/New_York). This governs the
# baseline/settlement session math in shared.calendar and is independent of the price provider: it
# stays America/New_York even though biquote stamps its daily bars at UTC midnight (the adapter maps
# a biquote bar's calendar date directly to the session date).
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


# Industry-sector equities. Each canonical sector asset is priced by a representative large-cap
# bellwether that biquote.io actually serves (sector ETFs and non-US listings are not served, so a
# bellwether stock is the pragmatic reference). (symbol, economic identity, NYSE|NASDAQ).
_EQUITY_BELLWETHERS: Mapping[AssetId, tuple[str, str, str]] = MappingProxyType(
    {
        AssetId.PHARMA: ("LLY", "Pharmaceutical sector (Eli Lilly bellwether)", "NYSE"),
        AssetId.DEFENSE_AEROSPACE: (
            "LMT",
            "Defense & aerospace sector (Lockheed Martin bellwether)",
            "NYSE",
        ),
        AssetId.AI_COMPUTE: ("NVDA", "AI compute sector (Nvidia bellwether)", "NASDAQ"),
        AssetId.SEMICONDUCTOR: (
            "TSM",
            "Semiconductor sector (TSMC bellwether)",
            "NYSE",
        ),
        AssetId.SOFTWARE: ("MSFT", "Software sector (Microsoft bellwether)", "NASDAQ"),
        AssetId.ENTERPRISE_SOFTWARE: (
            "ORCL",
            "Enterprise software sector (Oracle bellwether)",
            "NYSE",
        ),
        AssetId.INTERNET_SEARCH: (
            "GOOGL",
            "Internet search sector (Alphabet bellwether)",
            "NASDAQ",
        ),
        AssetId.CONSUMER_ELECTRONICS: (
            "AAPL",
            "Consumer electronics sector (Apple bellwether)",
            "NASDAQ",
        ),
        AssetId.BANKING: ("JPM", "Banking sector (JPMorgan bellwether)", "NYSE"),
        AssetId.PAYMENTS_FINANCE: (
            "V",
            "Payments & finance sector (Visa bellwether)",
            "NYSE",
        ),
        AssetId.AUTOMOTIVE: ("TSLA", "Automotive sector (Tesla bellwether)", "NASDAQ"),
        AssetId.FOOD_BEVERAGE: (
            "KO",
            "Food & beverage sector (Coca-Cola bellwether)",
            "NYSE",
        ),
        AssetId.REAL_ESTATE: (
            "AMT",
            "Real estate sector (American Tower REIT bellwether)",
            "NYSE",
        ),
        AssetId.INDUSTRIAL: ("MMM", "Industrial manufacturing sector (3M bellwether)", "NYSE"),
        AssetId.APPAREL: ("NKE", "Apparel sector (Nike bellwether)", "NYSE"),
    }
)


def _equity_series() -> dict[AssetId, AssetReferenceSeries]:
    """Build the sector-equity reference entries from the bellwether table."""
    return {
        asset_id: AssetReferenceSeries(
            asset_id=asset_id,
            provider="biquote.io",
            provider_symbol=symbol,
            economic_identity=identity,
            expected_exchange=exchange,
            timezone="America/New_York",
            currency="USD",
            price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
            is_adjusted=False,
            rollover_policy=ROLLOVER_INCLUDE_ALL,
            fallback=None,
            registry_version=REGISTRY_VERSION,
        )
        for asset_id, (symbol, identity, exchange) in _EQUITY_BELLWETHERS.items()
    }


_REGISTRY: Mapping[AssetId, AssetReferenceSeries] = MappingProxyType(
    {
        AssetId.GOLD: AssetReferenceSeries(
            asset_id=AssetId.GOLD,
            provider="biquote.io",
            provider_symbol="XAUUSD",
            economic_identity="Spot Gold vs US Dollar (XAU/USD) reference",
            expected_exchange="COMEX",
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
            provider="biquote.io",
            provider_symbol="UKOIL",
            economic_identity="Crude Oil Brent (UKOIL) continuous reference",
            expected_exchange="NYMEX",
            timezone="America/New_York",
            currency="USD",
            price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
            is_adjusted=False,
            rollover_policy=ROLLOVER_INCLUDE_ALL,
            fallback=None,
            registry_version=REGISTRY_VERSION,
        ),
        **_equity_series(),
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
