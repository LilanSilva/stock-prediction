"""``AssetId``: a registry-validated canonical asset identifier.

Replaces the former closed ``StrEnum`` so companies and markets can be added by editing the JSON
registry (``ASSET_REGISTRY_PATH``) without a code change or a database migration. Asset ids are
already stored as ``TEXT`` in Postgres and as ``Asset.id`` in Neo4j, so nothing downstream changes.

The public surface is deliberately enum-compatible so existing call sites keep working:

    AssetId.GOLD          -> attribute access (raises AttributeError when unknown)
    AssetId("GOLD")       -> construction   (raises ValueError when unknown)
    asset.value           -> the plain string
    list(AssetId)         -> every asset in registry order

Because it subclasses ``str``, an ``AssetId`` is accepted anywhere a string is, and comparisons
against plain strings behave as they did with ``StrEnum``.

Trade-off accepted: mypy can no longer prove exhaustive coverage over assets, because membership is
data-driven rather than declared in code. Unknown ids are still rejected -- at construction and at
every message boundary -- so an unrecognised asset is never silently accepted.
"""

from __future__ import annotations

from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from shared.reference.loader import asset_ids, is_known_asset


class _AssetIdMeta(type):
    """Gives the class enum-like ``AssetId.GOLD`` access, iteration, and membership."""

    def __getattr__(cls, name: str) -> AssetId:
        # Dunder/private lookups must behave normally so copy, pickle and Pydantic introspection
        # do not accidentally resolve to an asset id.
        if name.startswith("_"):
            raise AttributeError(name)
        if not is_known_asset(name):
            raise AttributeError(
                f"{name!r} is not a canonical asset id in the loaded registry "
                f"(check assets.json / ASSET_REGISTRY_PATH)"
            )
        return AssetId(name)

    def __iter__(cls) -> Any:
        return iter(AssetId(value) for value in asset_ids())

    def __len__(cls) -> int:
        return len(asset_ids())

    def __contains__(cls, item: object) -> bool:
        return isinstance(item, str) and is_known_asset(str(item))


class AssetId(str, metaclass=_AssetIdMeta):
    """A canonical asset id validated against the loaded registry."""

    __slots__ = ()

    # Instances are interned so each asset id is a singleton, exactly as StrEnum members were.
    # Identity comparisons (`asset is AssetId.GOLD`) therefore keep working.
    _interned: dict[str, AssetId] = {}

    def __new__(cls, value: object) -> AssetId:
        text = str(value)
        cached = cls._interned.get(text)
        if cached is not None:
            return cached
        if not is_known_asset(text):
            raise ValueError(
                f"unknown asset_id {text!r}; declare it in the asset registry "
                f"(assets.json / ASSET_REGISTRY_PATH)"
            )
        instance = super().__new__(cls, text)
        cls._interned[text] = instance
        return instance

    @property
    def value(self) -> str:
        """The plain string form (kept for parity with the former ``StrEnum``)."""
        return str(self)

    @property
    def name(self) -> str:
        """The identifier form; identical to ``value`` for registry-backed ids."""
        return str(self)

    def __repr__(self) -> str:
        return f"AssetId({str(self)!r})"

    @classmethod
    def _clear_intern_cache(cls) -> None:
        """Drop interned instances so a reloaded registry cannot leave a stale id valid."""
        cls._interned.clear()

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Validate inbound strings against the registry and serialise back to a plain string."""

        def _validate(value: object) -> AssetId:
            if isinstance(value, AssetId):
                return value
            if not isinstance(value, str):
                raise ValueError(f"asset_id must be a string, got {type(value).__name__}")
            return cls(value)

        return core_schema.no_info_plain_validator_function(
            _validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=core_schema.str_schema(), when_used="always"
            ),
        )
