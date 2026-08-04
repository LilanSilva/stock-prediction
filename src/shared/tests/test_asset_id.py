"""Tests for the registry-validated AssetId (formerly a closed StrEnum).

The behaviours asserted here are the compatibility contract that let ~100 existing call sites keep
working unchanged after the enum was replaced.
"""

from __future__ import annotations

import pytest

from shared.reference import supported_assets
from shared.schemas.messages import AssetId


def test_attribute_access_behaves_like_an_enum_member() -> None:
    assert AssetId.GOLD == "GOLD"
    assert AssetId.GOLD.value == "GOLD"
    assert AssetId.GOLD.name == "GOLD"


def test_members_are_interned_so_identity_comparison_holds() -> None:
    # StrEnum members were singletons; call sites use `is` comparisons.
    assert AssetId("GOLD") is AssetId.GOLD
    assert AssetId(AssetId.GOLD) is AssetId.GOLD


def test_is_a_str_subclass() -> None:
    assert isinstance(AssetId.GOLD, str)
    assert f"{AssetId.GOLD}" == "GOLD"
    assert AssetId.GOLD.lower() == "gold"


def test_usable_as_a_dict_key_alongside_plain_strings() -> None:
    mapping = {AssetId.GOLD: 1}
    assert mapping[AssetId("GOLD")] == 1
    assert mapping["GOLD"] == 1


def test_iteration_and_membership() -> None:
    assert len(AssetId) == len(supported_assets())
    assert set(AssetId) == {AssetId(a) for a in supported_assets()}
    assert "GOLD" in AssetId
    assert "NOT_AN_ASSET" not in AssetId


def test_unknown_id_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="unknown asset_id"):
        AssetId("GC=F")


def test_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="not a canonical asset id"):
        _ = AssetId.NOT_AN_ASSET


def test_dunder_attributes_are_not_treated_as_asset_ids() -> None:
    # Guards copy/pickle/pydantic introspection against resolving to a bogus asset.
    with pytest.raises(AttributeError):
        _ = AssetId.__deepcopy__


def test_file_driven_assets_are_valid_without_a_code_change() -> None:
    # The point of the refactor: these ids exist only in assets.json, never in Python.
    assert AssetId("SAAB_B_STO").value == "SAAB_B_STO"
    assert AssetId("NOVO_B_CPH") in set(AssetId)
