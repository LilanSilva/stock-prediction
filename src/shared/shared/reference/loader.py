"""JSON asset-registry loader: parsing, validation, and the in-process cache.

This module deliberately imports nothing from ``shared.schemas`` so that ``messages.AssetId`` can
validate against the registry without an import cycle (messages -> loader is one-way).

Resolution order for the registry file:

  1. ``ASSET_REGISTRY_PATH`` when set (the deployed case: a read-only mount, editable without a
     rebuild).
  2. ``assets.json`` packaged next to this module (the fallback baked into the image).

A malformed file raises :class:`InvalidRegistryError` at load time so a service refuses to boot
rather than silently running on stale or partial reference data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

ENV_REGISTRY_PATH = "ASSET_REGISTRY_PATH"
PACKAGED_REGISTRY = Path(__file__).with_name("assets.json")

# Providers with a Market Data adapter. An unknown provider is rejected at load time rather than
# surfacing later as an unroutable price request.
KNOWN_PROVIDERS = frozenset({"biquote.io", "yahoo"})

_REQUIRED_ASSET_FIELDS = (
    "asset_id",
    "name",
    "code",
    "provider",
    "provider_symbol",
    "economic_identity",
    "expected_exchange",
    "currency",
    "timezone",
    "session_complete_at",
)


class InvalidRegistryError(Exception):
    """Raised when the registry file is missing, unparseable, or internally inconsistent."""


@dataclass(frozen=True)
class AssetEntry:
    """One tradeable asset: a company listing or a commodity reference series.

    ``code`` is the human-readable ``EXCHANGE:TICKER`` form and is documentation only -- it is never
    sent to a provider. ``provider_symbol`` is the exact string the provider expects, which differs
    per vendor for the same instrument (Yahoo ``SAAB-B.ST`` vs Finnhub ``SAAB B.ST``).
    """

    asset_id: str
    name: str
    code: str
    provider: str
    provider_symbol: str
    economic_identity: str
    expected_exchange: str
    currency: str
    timezone: str
    session_complete_at: str
    keywords: tuple[str, ...] = ()
    price_kind: str = "PROVIDER_DAILY_CLOSE"
    is_adjusted: bool = False
    fallback: str | None = None
    # Every asset belongs to exactly one group, so this is never None once loaded.
    group_id: str = ""

    @property
    def session_hour(self) -> int:
        return int(self.session_complete_at.split(":")[0])

    @property
    def session_minute(self) -> int:
        return int(self.session_complete_at.split(":")[1])


@dataclass(frozen=True)
class AssetGroup:
    """A group: a fan-out target for industry-scope news, never itself tradeable.

    Every asset belongs to exactly one group -- commodities included (GOLD sits in
    ``PRECIOUS_METALS``), so there is a single uniform shape rather than a special case.
    """

    group_id: str
    display_name: str
    industry_keywords: tuple[str, ...] = ()
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class Registry:
    """The loaded, validated registry."""

    registry_version: str
    rollover_policy: str
    assets: dict[str, AssetEntry] = field(default_factory=dict)
    groups: dict[str, AssetGroup] = field(default_factory=dict)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidRegistryError(message)


def _parse_session_close(raw: object, asset_id: str) -> str:
    _require(
        isinstance(raw, str) and raw.count(":") == 1,
        f"{asset_id}: session_complete_at must be 'HH:MM', got {raw!r}",
    )
    text = str(raw)
    hour, minute = text.split(":")
    _require(
        hour.isdigit() and minute.isdigit() and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59,
        f"{asset_id}: session_complete_at is not a valid time: {text!r}",
    )
    return text


def _keywords(raw: object, asset_id: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    _require(isinstance(raw, list), f"{asset_id}: keywords must be a list")
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        _require(
            isinstance(item, str) and item.strip() != "",
            f"{asset_id}: keywords must be non-empty strings",
        )
        # Keywords are matched case-insensitively downstream; normalise once here.
        out.append(str(item).strip().lower())
    return tuple(out)


def _build_asset(raw: object, *, group_id: str) -> AssetEntry:
    _require(isinstance(raw, dict), "each asset entry must be an object")
    data: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    asset_id = str(data.get("asset_id", "<missing>"))

    missing = [key for key in _REQUIRED_ASSET_FIELDS if not data.get(key)]
    _require(not missing, f"{asset_id}: missing required field(s): {', '.join(missing)}")

    # A colon would leak the exchange into business identities: asset_id is stored as TEXT in
    # Postgres, as Asset.id in Neo4j, and embedded in the prediction idempotency key.
    _require(
        ":" not in asset_id, f"{asset_id}: asset_id must not contain ':' (use 'code' for that)"
    )
    _require(
        asset_id.replace("_", "").isalnum() and asset_id == asset_id.upper(),
        f"{asset_id}: asset_id must be UPPER_SNAKE_CASE alphanumerics",
    )
    provider = str(data["provider"])
    _require(
        provider in KNOWN_PROVIDERS,
        f"{asset_id}: unknown provider {provider!r} (known: {', '.join(sorted(KNOWN_PROVIDERS))})",
    )

    return AssetEntry(
        asset_id=asset_id,
        name=str(data["name"]),
        code=str(data["code"]),
        provider=provider,
        provider_symbol=str(data["provider_symbol"]),
        economic_identity=str(data["economic_identity"]),
        expected_exchange=str(data["expected_exchange"]),
        currency=str(data["currency"]),
        timezone=str(data["timezone"]),
        session_complete_at=_parse_session_close(data["session_complete_at"], asset_id),
        keywords=_keywords(data.get("keywords"), asset_id),
        price_kind=str(data.get("price_kind", "PROVIDER_DAILY_CLOSE")),
        is_adjusted=bool(data.get("is_adjusted", False)),
        fallback=None if data.get("fallback") in (None, "") else str(data["fallback"]),
        group_id=group_id,
    )


def parse_registry(payload: object) -> Registry:
    """Validate a decoded registry document. Raises InvalidRegistryError on any inconsistency."""
    _require(isinstance(payload, dict), "registry root must be an object")
    doc: dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}

    version = doc.get("registry_version")
    _require(bool(version), "registry_version is required")
    rollover = doc.get("rollover_policy")
    _require(bool(rollover), "rollover_policy is required")

    assets: dict[str, AssetEntry] = {}
    groups: dict[str, AssetGroup] = {}

    def add(entry: AssetEntry) -> None:
        _require(
            entry.asset_id not in assets, f"duplicate asset_id {entry.asset_id!r} in the registry"
        )
        assets[entry.asset_id] = entry

    for raw_group in doc.get("groups") or []:
        _require(isinstance(raw_group, dict), "each group entry must be an object")
        group: dict[str, Any] = dict(raw_group) if isinstance(raw_group, dict) else {}
        group_id = str(group.get("group_id", "<missing>"))
        _require(bool(group.get("group_id")), "group_id is required for every group")
        _require(bool(group.get("display_name")), f"{group_id}: display_name is required")
        _require(group_id not in groups, f"duplicate group_id {group_id!r}")

        members: list[str] = []
        members_raw = group.get("assets") or []
        _require(isinstance(members_raw, list), f"{group_id}: assets must be a list")
        for raw_asset in members_raw:
            entry = _build_asset(raw_asset, group_id=group_id)
            add(entry)
            members.append(entry.asset_id)
        _require(bool(members), f"{group_id}: a group must declare at least one asset")

        groups[group_id] = AssetGroup(
            group_id=group_id,
            display_name=str(group["display_name"]),
            industry_keywords=_keywords(group.get("industry_keywords"), group_id),
            members=tuple(members),
        )

    _require(bool(assets), "the registry declares no assets")

    # A group_id must not collide with an asset_id: both are matched against the same identifiers
    # when resolving scope, so an overlap would be ambiguous.
    overlap = sorted(set(groups) & set(assets))
    _require(not overlap, f"group_id/asset_id collision: {', '.join(overlap)}")

    # A company keyword claimed by two assets makes company-scope resolution ambiguous.
    claims: dict[str, list[str]] = {}
    for entry in assets.values():
        for keyword in entry.keywords:
            claims.setdefault(keyword, []).append(entry.asset_id)
    clashes = {kw: ids for kw, ids in claims.items() if len(ids) > 1}
    _require(
        not clashes,
        "keyword claimed by multiple assets: "
        + "; ".join(f"{kw!r} -> {', '.join(sorted(ids))}" for kw, ids in sorted(clashes.items())),
    )

    # A declared fallback must itself be a known asset; silent substitution is never permitted.
    for entry in assets.values():
        if entry.fallback is not None:
            _require(
                entry.fallback in assets,
                f"{entry.asset_id}: fallback {entry.fallback!r} is not a declared asset",
            )

    return Registry(
        registry_version=str(version),
        rollover_policy=str(rollover),
        assets=assets,
        groups=groups,
    )


def registry_path() -> Path:
    """The registry file this process will load (env override, else the packaged default)."""
    override = os.environ.get(ENV_REGISTRY_PATH)
    return Path(override) if override else PACKAGED_REGISTRY


def load_registry(path: Path | None = None) -> Registry:
    """Read and validate the registry from ``path`` (uncached)."""
    target = path or registry_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidRegistryError(f"cannot read asset registry at {target}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidRegistryError(f"asset registry at {target} is not valid JSON: {exc}") from exc
    return parse_registry(payload)


@lru_cache(maxsize=1)
def registry() -> Registry:
    """The process-wide registry, loaded once.

    Cached deliberately: it is read on every message path, and swapping reference data mid-flight
    would let one message validate against a different asset set than the one that produced it.
    """
    return load_registry()


def reset_cache() -> None:
    """Drop the cached registry (tests and the validation script only).

    Also clears ``AssetId``'s interned instances, so an id that was valid under the previous
    registry cannot survive a reload as a stale singleton. Imported lazily to keep this module free
    of any ``shared.schemas`` dependency (the import runs one way: schemas -> loader).
    """
    registry.cache_clear()
    from shared.schemas.asset_id import AssetId

    AssetId._clear_intern_cache()


def is_known_asset(asset_id: str) -> bool:
    return asset_id in registry().assets


def asset_ids() -> tuple[str, ...]:
    return tuple(registry().assets)
