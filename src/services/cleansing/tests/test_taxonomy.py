from __future__ import annotations

from shared.schemas.messages import AssetId, ConditionCode, EventPolarity, EventType

from cleansing.taxonomy import (
    assets_for_event_type,
    classify_polarity,
    classify_text,
    gate2_compatible,
    infer_assets,
    infer_conditions,
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


def test_classify_polarity_resolution_english() -> None:
    assert classify_polarity("USA calls off Iran attack") == EventPolarity.RESOLUTION
    assert classify_polarity("Ceasefire agreed after talks") == EventPolarity.RESOLUTION
    assert classify_polarity("Both sides cancel the planned strike") == EventPolarity.RESOLUTION


def test_classify_polarity_resolution_swedish() -> None:
    assert classify_polarity("USA blåser av attacken mot Iran") == EventPolarity.RESOLUTION
    assert classify_polarity("Parterna avbryter konflikten") == EventPolarity.RESOLUTION


def test_classify_polarity_defaults_to_occurrence() -> None:
    assert classify_polarity("Iran launches an attack on shipping") == EventPolarity.OCCURRENCE
    assert classify_polarity("An ordinary market update") == EventPolarity.OCCURRENCE


def test_infer_conditions_transport_cue() -> None:
    tags = infer_conditions("Tanker blocked in the Strait of Hormuz", EventType.MILITARY_CONFLICT)
    assert tags == [ConditionCode.TRANSPORT_AFFECTED]


def test_infer_conditions_geopolitical_without_transport_is_safe_haven() -> None:
    tags = infer_conditions("USA calls off Iran attack", EventType.MILITARY_CONFLICT)
    assert tags == [ConditionCode.SAFE_HAVEN_ONLY]
    assert infer_conditions("New sanctions on the regime", EventType.SANCTIONS) == [
        ConditionCode.SAFE_HAVEN_ONLY
    ]


def test_infer_conditions_transport_wins_over_safe_haven() -> None:
    # A geopolitical event with a transport cue is TRANSPORT_AFFECTED, never SAFE_HAVEN_ONLY.
    tags = infer_conditions("Conflict shuts a key oil pipeline", EventType.MILITARY_CONFLICT)
    assert tags == [ConditionCode.TRANSPORT_AFFECTED]


def test_infer_conditions_non_geopolitical_without_cue_is_empty() -> None:
    assert infer_conditions("Central bank raises interest rate", EventType.RATE_DECISION) == []


def test_infer_conditions_never_returns_risk_premium() -> None:
    tags = infer_conditions("Tanker blocked near port", EventType.SANCTIONS)
    assert ConditionCode.RISK_PREMIUM_ELEVATED not in tags


def test_assets_for_event_type_geopolitical() -> None:
    assert assets_for_event_type(EventType.MILITARY_CONFLICT) == (
        AssetId.GOLD,
        AssetId.BRENT_OIL,
    )
    assert assets_for_event_type(EventType.SUPPLY_DISRUPTION) == (AssetId.BRENT_OIL,)
    assert assets_for_event_type(EventType.INFLATION_CHANGE) == (AssetId.GOLD,)


def test_assets_for_event_type_unmapped_is_empty() -> None:
    assert assets_for_event_type(EventType.OTHER) == ()
    assert assets_for_event_type(EventType.CORPORATE_EARNINGS) == ()
