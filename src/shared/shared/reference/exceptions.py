"""Typed errors for the asset reference registry."""

from __future__ import annotations


class RegistryError(Exception):
    """Base class for asset-registry lookup failures."""


class UnknownAssetError(RegistryError):
    """Raised when a canonical asset ID has no approved reference series."""

    def __init__(self, asset_id: str) -> None:
        super().__init__(f"no approved reference series for asset_id={asset_id!r}")
        self.asset_id = asset_id
