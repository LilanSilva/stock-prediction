"""Tests for the JSON asset-registry loader and the registry-validated AssetId."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shared.reference.loader import (
    InvalidRegistryError,
    load_registry,
    parse_registry,
    registry_path,
)


def _asset(**overrides: Any) -> dict[str, Any]:
    base = {
        "asset_id": "ACME_NYSE",
        "name": "Acme",
        "code": "NYSE:ACME",
        "provider": "biquote.io",
        "provider_symbol": "ACME",
        "economic_identity": "Acme Corp ordinary shares",
        "expected_exchange": "NYSE",
        "currency": "USD",
        "timezone": "America/New_York",
        "session_complete_at": "17:00",
        "keywords": ["acme"],
    }
    base.update(overrides)
    return base


def _doc(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "registry_version": "test-v1",
        "rollover_policy": "PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1",
        "groups": [
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "industry_keywords": ["widget"],
                "assets": [_asset()],
            }
        ],
    }
    base.update(overrides)
    return base


# --- happy path ---------------------------------------------------------------------------------


def test_parses_groups_and_members() -> None:
    reg = parse_registry(_doc())
    assert reg.registry_version == "test-v1"
    assert set(reg.assets) == {"ACME_NYSE"}
    assert reg.groups["WIDGETS"].members == ("ACME_NYSE",)
    assert reg.assets["ACME_NYSE"].group_id == "WIDGETS"


def test_every_asset_carries_its_group_id() -> None:
    # Commodities are grouped like everything else, so nothing downstream needs a special case.
    reg = parse_registry(_doc())
    assert all(entry.group_id for entry in reg.assets.values())


def test_keywords_are_lowercased_for_matching() -> None:
    doc = _doc(
        groups=[
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "industry_keywords": ["Widget", " GADGET "],
                "assets": [_asset(keywords=["Acme", "ACME CORP"])],
            }
        ]
    )
    reg = parse_registry(doc)
    assert reg.assets["ACME_NYSE"].keywords == ("acme", "acme corp")
    assert reg.groups["WIDGETS"].industry_keywords == ("widget", "gadget")


def test_session_close_is_split_into_hour_and_minute() -> None:
    doc = _doc(
        groups=[
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "assets": [_asset(session_complete_at="18:30")],
            }
        ]
    )
    entry = parse_registry(doc).assets["ACME_NYSE"]
    assert (entry.session_hour, entry.session_minute) == (18, 30)


# --- structural rejection -----------------------------------------------------------------------


def test_rejects_duplicate_asset_id() -> None:
    doc = _doc(
        groups=[
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "assets": [_asset(), _asset(keywords=["other"])],
            }
        ]
    )
    with pytest.raises(InvalidRegistryError, match="duplicate asset_id"):
        parse_registry(doc)


def test_rejects_colon_in_asset_id() -> None:
    # A colon would leak the exchange into business identities (Postgres TEXT, Neo4j Asset.id, and
    # the prediction idempotency key).
    doc = _doc(
        groups=[
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "assets": [_asset(asset_id="NYSE:ACME")],
            }
        ]
    )
    with pytest.raises(InvalidRegistryError, match="must not contain"):
        parse_registry(doc)


def test_rejects_keyword_claimed_by_two_assets() -> None:
    # An ambiguous keyword would make company-scope resolution non-deterministic.
    doc = _doc(
        groups=[
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "assets": [
                    _asset(keywords=["shared"]),
                    _asset(asset_id="OTHER_NYSE", provider_symbol="OTH", keywords=["shared"]),
                ],
            }
        ]
    )
    with pytest.raises(InvalidRegistryError, match="claimed by multiple assets"):
        parse_registry(doc)


def test_rejects_unknown_provider() -> None:
    doc = _doc(
        groups=[
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "assets": [_asset(provider="madeup")],
            }
        ]
    )
    with pytest.raises(InvalidRegistryError, match="unknown provider"):
        parse_registry(doc)


def test_rejects_missing_required_field() -> None:
    incomplete = _asset()
    del incomplete["currency"]
    doc = _doc(
        groups=[
            {"group_id": "WIDGETS", "display_name": "Widgets", "assets": [incomplete]}
        ]
    )
    with pytest.raises(InvalidRegistryError, match="missing required field"):
        parse_registry(doc)


def test_rejects_group_id_colliding_with_asset_id() -> None:
    doc = _doc(
        groups=[
            {
                "group_id": "ACME_NYSE",
                "display_name": "Collides",
                "assets": [_asset()],
            }
        ]
    )
    with pytest.raises(InvalidRegistryError, match="collision"):
        parse_registry(doc)


def test_rejects_empty_group() -> None:
    doc = _doc(groups=[{"group_id": "WIDGETS", "display_name": "Widgets", "assets": []}])
    with pytest.raises(InvalidRegistryError, match="at least one asset"):
        parse_registry(doc)


def test_rejects_fallback_to_undeclared_asset() -> None:
    doc = _doc(
        groups=[
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "assets": [_asset(fallback="NOT_DECLARED")],
            }
        ]
    )
    with pytest.raises(InvalidRegistryError, match="is not a declared asset"):
        parse_registry(doc)


def test_rejects_bad_session_time() -> None:
    doc = _doc(
        groups=[
            {
                "group_id": "WIDGETS",
                "display_name": "Widgets",
                "assets": [_asset(session_complete_at="25:00")],
            }
        ]
    )
    with pytest.raises(InvalidRegistryError, match="not a valid time"):
        parse_registry(doc)


def test_rejects_registry_with_no_assets() -> None:
    with pytest.raises(InvalidRegistryError, match="no assets"):
        parse_registry({"registry_version": "v", "rollover_policy": "p"})


# --- file loading -------------------------------------------------------------------------------


def test_load_registry_reports_unparseable_json(tmp_path: Path) -> None:
    target = tmp_path / "assets.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(InvalidRegistryError, match="not valid JSON"):
        load_registry(target)


def test_load_registry_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InvalidRegistryError, match="cannot read"):
        load_registry(tmp_path / "absent.json")


def test_load_registry_reads_a_file(tmp_path: Path) -> None:
    target = tmp_path / "assets.json"
    target.write_text(json.dumps(_doc()), encoding="utf-8")
    assert set(load_registry(target).assets) == {"ACME_NYSE"}


def test_registry_path_honours_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "custom.json"
    monkeypatch.setenv("ASSET_REGISTRY_PATH", str(override))
    assert registry_path() == override


def test_registry_path_falls_back_to_packaged_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASSET_REGISTRY_PATH", raising=False)
    assert registry_path().name == "assets.json"
    assert registry_path().is_file()


# --- the shipped registry -----------------------------------------------------------------------


def test_packaged_registry_is_valid() -> None:
    reg = load_registry()
    assert reg.assets
    assert reg.groups
    # Every group member must resolve to a declared asset.
    for group in reg.groups.values():
        for member in group.members:
            assert member in reg.assets


def test_packaged_registry_routes_every_asset_to_a_known_provider() -> None:
    providers = {entry.provider for entry in load_registry().assets.values()}
    assert providers == {"yahoo"}


def test_packaged_registry_has_non_us_assets_with_local_timezones() -> None:
    entries = load_registry().assets.values()
    stockholm = [e for e in entries if e.timezone == "Europe/Stockholm"]
    assert stockholm, "expected at least one Stockholm-listed asset"
    assert all(e.currency == "SEK" for e in stockholm)
    assert all(e.provider == "yahoo" for e in stockholm)
