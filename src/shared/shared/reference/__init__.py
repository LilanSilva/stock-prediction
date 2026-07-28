"""Shared reference data: the executable asset registry.

The registry is the single source of truth mapping canonical `AssetId` values to their approved,
versioned provider reference series (frozen policy `poc6-yahoo-reference-v1`).
"""

from __future__ import annotations

from shared.reference.asset_registry import (
    REGISTRY_VERSION,
    ROLLOVER_INCLUDE_ALL,
    SESSION_COMPLETION_HOUR,
    SESSION_COMPLETION_MINUTE,
    AssetReferenceSeries,
    resolve,
    supported_assets,
)
from shared.reference.exceptions import RegistryError, UnknownAssetError

__all__ = [
    "REGISTRY_VERSION",
    "ROLLOVER_INCLUDE_ALL",
    "SESSION_COMPLETION_HOUR",
    "SESSION_COMPLETION_MINUTE",
    "AssetReferenceSeries",
    "RegistryError",
    "UnknownAssetError",
    "resolve",
    "supported_assets",
]
