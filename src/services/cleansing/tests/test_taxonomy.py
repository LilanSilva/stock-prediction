from __future__ import annotations

from shared.schemas.messages import AssetId, EventType

from cleansing.taxonomy import (
    classify_text,
    gate2_compatible,
    infer_assets,
    map_action,
)


def test_map_action_english() -> None:
    assert map_action("attack") == EventType.MILITARY_CONFLICT
    assert map_action("sanctions") == EventType.SANCTIONS


def test_map_action_swedish() -> None:
    assert map_action("anfall") == EventType.MILITARY_CONFLICT
    assert map_action("sanktioner") == EventType.SANCTIONS


def test_unmapped_action_is_other() -> None:
    assert map_action("frolic") == EventType.OTHER
    assert map_action(None) == EventType.OTHER


def test_classify_text_finds_phrase() -> None:
    event_type, keyword = classify_text("The Fed will raise interest rate this week")
    assert event_type == EventType.RATE_DECISION
    assert keyword == "interest rate"


def test_classify_text_swedish_rate() -> None:
    event_type, _ = classify_text("Riksbanken höjer ränta med 25 punkter")
    assert event_type == EventType.RATE_DECISION


def test_classify_text_unmatched_is_other() -> None:
    event_type, keyword = classify_text("A pleasant day in the park")
    assert event_type == EventType.OTHER
    assert keyword is None


def test_infer_assets() -> None:
    assert infer_assets("Brent crude oil climbs on OPEC news") == (AssetId.BRENT_OIL,)
    assert infer_assets("Gold and guld rally") == (AssetId.GOLD,)
    assert set(infer_assets("oil and gold both move")) == {AssetId.GOLD, AssetId.BRENT_OIL}


def test_gate2_conservative() -> None:
    # Same known type is compatible.
    assert gate2_compatible(EventType.RATE_DECISION, EventType.RATE_DECISION)
    # Distinct types never merge.
    assert not gate2_compatible(EventType.MILITARY_CONFLICT, EventType.STRAIT_CLOSURE)
    # OTHER is never compatible, even with another OTHER.
    assert not gate2_compatible(EventType.OTHER, EventType.OTHER)
