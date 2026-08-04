"""Executable asset reference registry (source of truth for provider mappings).

Reference data is loaded from the JSON registry (``shared.reference.loader``) so companies and
markets can be added by editing ``assets.json`` -- or a file pointed at by
``ASSET_REGISTRY_PATH`` -- without a code change, a redeploy of the shared library, or a database
migration. Asset ids are already ``TEXT`` in Postgres and ``Asset.id`` in Neo4j.

Business logic uses canonical asset ids. Provider symbols such as ``XAUUSD``/``SAAB-B.ST`` live only
in the registry file and inside market-data adapters; they never cross a service boundary as a
business identity. The human-readable ``EXCHANGE:TICKER`` form is carried as ``code`` for
documentation only -- biquote rejects that format outright, so it is never sent to a provider.

Policy invariants (carried over from P06/T03):
  - Provider daily bars are PROVIDER_DAILY_CLOSE, never labelled official exchange settlements.
  - Continuous series use PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1 (include every positive raw
    close; no adjustment or outcome-based exclusion).
  - A fallback must name a declared asset, or be null. No silent substitution.

Provider routing (S4 of the multi-market feature):
  - ``biquote.io`` serves a curated list of US mega-caps only. Probed 2026-08-03: every European
    listing returns 0 bars, including EU giants on US exchanges (ASML, SAP, NVO, SHEL), so the limit
    is the vendor's symbol list rather than the exchange.
  - ``yahoo`` serves Stockholm and Euronext daily closes and is used for non-US assets. The HTTP
    429s that retired Yahoo in POC-7 were caused by its custom User-Agent
    (``feed-analyzer-poc6/1.0``), not by IP rate limiting: 40 concurrent requests with a browser
    User-Agent all return 200, while 8 with curl's default agent all return 429. The adapter must
    therefore send a browser User-Agent.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from shared.reference.exceptions import UnknownAssetError
from shared.reference.loader import AssetEntry, AssetGroup, registry

# The continuous-futures rollover policy pre-declared by P06/T03. Declared before any outcome is
# evaluated, so an observation may be validated against it without look-ahead.
ROLLOVER_INCLUDE_ALL = "PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1"

# Default session-completion wall-clock (17:00 local) for assets that do not override it. Per-asset
# values come from the registry: Stockholm and Euronext complete later than New York.
SESSION_COMPLETION_HOUR = 17
SESSION_COMPLETION_MINUTE = 0


class AssetReferenceSeries(BaseModel):
    """Approved provider reference series for one canonical asset.

    Immutable. ``fallback`` is the canonical asset ID of an economically equivalent instrument, or
    None when no fallback has been validated. No silent substitution is permitted.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    asset_id: str
    provider: str = Field(min_length=1)
    provider_symbol: str = Field(min_length=1)
    economic_identity: str = Field(min_length=1)
    expected_exchange: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    price_kind: str
    is_adjusted: bool
    rollover_policy: str = Field(min_length=1)
    fallback: str | None
    registry_version: str = Field(min_length=1)
    # Local wall-clock at which the session's daily close is final.
    session_completion_hour: int = Field(ge=0, le=23)
    session_completion_minute: int = Field(ge=0, le=59)
    # Human-readable EXCHANGE:TICKER form; documentation only, never sent to a provider.
    code: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    # The group this asset belongs to; every asset has exactly one.
    group_id: str = Field(min_length=1)


def _to_series(entry: AssetEntry, registry_version: str, rollover_policy: str) -> (
    AssetReferenceSeries
):
    return AssetReferenceSeries(
        asset_id=entry.asset_id,
        provider=entry.provider,
        provider_symbol=entry.provider_symbol,
        economic_identity=entry.economic_identity,
        expected_exchange=entry.expected_exchange,
        timezone=entry.timezone,
        currency=entry.currency,
        price_kind=entry.price_kind,
        is_adjusted=entry.is_adjusted,
        rollover_policy=rollover_policy,
        fallback=entry.fallback,
        registry_version=registry_version,
        session_completion_hour=entry.session_hour,
        session_completion_minute=entry.session_minute,
        code=entry.code,
        display_name=entry.name,
        group_id=entry.group_id,
    )


def registry_version() -> str:
    """The loaded registry's version string (was the ``REGISTRY_VERSION`` constant)."""
    return registry().registry_version


# Module-level attribute access keeps `from shared.reference.asset_registry import REGISTRY_VERSION`
# working: the version now comes from the registry file, so it cannot be a module constant evaluated
# at import time without forcing a load before ASSET_REGISTRY_PATH has been read.
def __getattr__(name: str) -> object:
    if name == "REGISTRY_VERSION":
        return registry_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def resolve(asset_id: str) -> AssetReferenceSeries:
    """Return the approved reference series for a canonical asset ID.

    Raises UnknownAssetError for any asset without an approved series, so unknown assets are never
    silently treated as valid.
    """
    reg = registry()
    entry = reg.assets.get(str(asset_id))
    if entry is None:
        raise UnknownAssetError(str(asset_id))
    return _to_series(entry, reg.registry_version, reg.rollover_policy)


def supported_assets() -> tuple[str, ...]:
    """Canonical asset IDs that currently have an approved reference series."""
    return tuple(registry().assets)


def group_of(asset_id: str) -> str:
    """The group an asset belongs to.

    Every asset has exactly one group -- commodities included (GOLD is in ``PRECIOUS_METALS``).
    Raises UnknownAssetError for an unknown asset rather than returning a falsy group.
    """
    reg = registry()
    entry = reg.assets.get(str(asset_id))
    if entry is None:
        raise UnknownAssetError(str(asset_id))
    return entry.group_id


def members_of(group_id: str) -> tuple[str, ...]:
    """Every canonical asset in an industry group; the fan-out target for industry-scope news."""
    group = registry().groups.get(str(group_id))
    return group.members if group is not None else ()


def groups() -> Mapping[str, AssetGroup]:
    """All industry groups, keyed by group_id."""
    return registry().groups


def provider_of(asset_id: str) -> str:
    """The Market Data adapter that prices this asset."""
    return resolve(asset_id).provider
