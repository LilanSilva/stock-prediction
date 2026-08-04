"""Shared reference data: the executable asset registry.

The registry maps canonical asset ids to their approved, versioned provider reference series. It is
loaded from JSON (`assets.json`, or `ASSET_REGISTRY_PATH`) so companies and markets can be added
without a code change; see `shared.reference.loader`.
"""

from __future__ import annotations

from typing import Any

from shared.reference.asset_registry import (
    ROLLOVER_INCLUDE_ALL,
    SESSION_COMPLETION_HOUR,
    SESSION_COMPLETION_MINUTE,
    AssetReferenceSeries,
    group_of,
    groups,
    members_of,
    provider_of,
    registry_version,
    resolve,
    supported_assets,
)
from shared.reference.exceptions import RegistryError, UnknownAssetError
from shared.reference.loader import (
    AssetEntry,
    AssetGroup,
    InvalidRegistryError,
    Registry,
    asset_ids,
    is_known_asset,
    load_registry,
    registry,
    registry_path,
    reset_cache,
)


# `REGISTRY_VERSION` was a module constant before the registry became file-driven. It is served
# lazily so importing this package does not force a registry load before ASSET_REGISTRY_PATH is
# read.
def __getattr__(name: str) -> Any:
    if name == "REGISTRY_VERSION":
        return registry_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "REGISTRY_VERSION",
    "ROLLOVER_INCLUDE_ALL",
    "SESSION_COMPLETION_HOUR",
    "SESSION_COMPLETION_MINUTE",
    "AssetEntry",
    "AssetGroup",
    "AssetReferenceSeries",
    "InvalidRegistryError",
    "Registry",
    "RegistryError",
    "UnknownAssetError",
    "asset_ids",
    "group_of",
    "groups",
    "is_known_asset",
    "load_registry",
    "members_of",
    "provider_of",
    "registry",
    "registry_path",
    "registry_version",
    "reset_cache",
    "resolve",
    "supported_assets",
]
