from __future__ import annotations

from shared.reference import members_of
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
    # A conflict moves the safe-haven commodities AND the weapons makers, across markets.
    conflict = assets_for_event_type(EventType.MILITARY_CONFLICT)
    assert {AssetId.GOLD, AssetId.BRENT_OIL} <= set(conflict)
    assert set(members_of("WEAPON_INDUSTRY")) <= set(conflict)

    # A supply disruption moves oil and the producers/refiners, not defence.
    disruption = assets_for_event_type(EventType.SUPPLY_DISRUPTION)
    assert AssetId.BRENT_OIL in disruption
    assert set(members_of("OIL_GAS")) <= set(disruption)
    assert AssetId.SAAB_B_STO not in disruption

    # An event type with no group mapping stays commodity-only.
    assert assets_for_event_type(EventType.INFLATION_CHANGE) == (AssetId.GOLD,)


def test_assets_for_event_type_unmapped_is_empty() -> None:
    assert assets_for_event_type(EventType.OTHER) == ()
    assert assets_for_event_type(EventType.CORPORATE_EARNINGS) == ()


# --- new event type classification tests ---


def test_classify_text_corporate_acquisition_english() -> None:
    assert classify_text("AstraZeneca in merger talks with rival")[0] == EventType.CORPORATE_ACQUISITION
    assert classify_text("Company announces acquisition of startup")[0] == EventType.CORPORATE_ACQUISITION
    assert classify_text("Hostile takeover bid rejected by board")[0] == EventType.CORPORATE_ACQUISITION


def test_classify_text_corporate_acquisition_swedish() -> None:
    assert classify_text("Uppgifter: Astra Zeneca i samtal om jättefusion")[0] == EventType.CORPORATE_ACQUISITION
    assert classify_text("Bolaget bekräftar förvärv av konkurrent")[0] == EventType.CORPORATE_ACQUISITION


def test_classify_text_executive_change_english() -> None:
    assert classify_text("Company CEO steps down amid scandal")[0] == EventType.EXECUTIVE_CHANGE
    assert classify_text("Board appoints new CFO after departure")[0] == EventType.EXECUTIVE_CHANGE


def test_classify_text_executive_change_swedish() -> None:
    assert classify_text("Bolagets ny vd presenteras på stämman")[0] == EventType.EXECUTIVE_CHANGE


def test_classify_text_regulatory_action_english() -> None:
    assert classify_text("FDA approval granted for new cancer drug")[0] == EventType.REGULATORY_ACTION
    assert classify_text("Regulator fined the bank 200 million")[0] == EventType.REGULATORY_ACTION


def test_classify_text_debt_crisis_english() -> None:
    assert classify_text("Company files for bankruptcy protection")[0] == EventType.DEBT_CRISIS
    assert classify_text("Firm declared insolvent by court")[0] == EventType.DEBT_CRISIS


def test_classify_text_debt_crisis_swedish() -> None:
    assert classify_text("Bolaget ansöker om konkurs")[0] == EventType.DEBT_CRISIS


def test_classify_text_restructuring_english() -> None:
    assert classify_text("Company announces major layoffs of 5000 workers")[0] == EventType.RESTRUCTURING
    assert classify_text("Firm plans spin-off of its energy division")[0] == EventType.RESTRUCTURING


def test_classify_text_restructuring_swedish() -> None:
    assert classify_text("Bolaget utfärdar varsel om uppsägning")[0] == EventType.RESTRUCTURING


def test_classify_text_legal_dispute_english() -> None:
    assert classify_text("Shareholders file class action lawsuit")[0] == EventType.LEGAL_DISPUTE
    assert classify_text("SEC opens fraud investigation into the firm")[0] == EventType.LEGAL_DISPUTE


def test_classify_text_product_recall_english() -> None:
    assert classify_text("Automaker recalls 500000 vehicles over safety")[0] == EventType.PRODUCT_RECALL
    assert classify_text("Drug withdrawn from market after safety warning")[0] == EventType.PRODUCT_RECALL


def test_classify_text_dividend_change_english() -> None:
    assert classify_text("Company cuts dividend by 50 percent")[0] == EventType.DIVIDEND_CHANGE
    assert classify_text("Board declares special dividend payout")[0] == EventType.DIVIDEND_CHANGE


def test_classify_text_contract_win_english() -> None:
    assert classify_text("Firm wins major defence contract worth 2 billion")[0] == EventType.CONTRACT_WIN
    assert classify_text("New supply agreement signed with automaker")[0] == EventType.CONTRACT_WIN


def test_classify_text_contract_win_swedish() -> None:
    assert classify_text("Bolaget tecknar nytt avtal med partnern")[0] == EventType.CONTRACT_WIN


def test_classify_text_share_buyback_english() -> None:
    assert classify_text("Company announces 1 billion share buyback programme")[0] == EventType.SHARE_BUYBACK
    assert classify_text("Board approves share repurchase of 500 million")[0] == EventType.SHARE_BUYBACK


def test_classify_text_ipo_listing_english() -> None:
    assert classify_text("Tech startup files for IPO on NASDAQ")[0] == EventType.IPO_LISTING
    assert classify_text("Company stock market debut expected next month")[0] == EventType.IPO_LISTING


def test_classify_text_ipo_listing_swedish() -> None:
    assert classify_text("Bolaget planerar börsnotering i höst")[0] == EventType.IPO_LISTING


def test_classify_text_cybersecurity_incident_english() -> None:
    assert classify_text("Bank suffers major data breach affecting millions")[0] == EventType.CYBERSECURITY_INCIDENT
    assert classify_text("Ransomware attack shuts down hospital systems")[0] == EventType.CYBERSECURITY_INCIDENT


def test_classify_text_trade_policy_english() -> None:
    assert classify_text("US imposes 25 percent tariff on steel imports")[0] == EventType.TRADE_POLICY
    assert classify_text("Trade war escalates between US and China")[0] == EventType.TRADE_POLICY


def test_classify_text_fiscal_policy_english() -> None:
    assert classify_text("Government announces major stimulus package")[0] == EventType.FISCAL_POLICY
    assert classify_text("UK chancellor delivers annual budget")[0] == EventType.FISCAL_POLICY


def test_classify_text_currency_crisis_english() -> None:
    assert classify_text("Turkish lira devalued after central bank move")[0] == EventType.CURRENCY_CRISIS
    assert classify_text("Currency collapse hits emerging markets")[0] == EventType.CURRENCY_CRISIS


def test_classify_text_geopolitical_tension_english() -> None:
    assert classify_text("China conducts military exercises near Taiwan")[0] == EventType.GEOPOLITICAL_TENSION
    assert classify_text("North Korea carries out missile test over sea")[0] == EventType.GEOPOLITICAL_TENSION


def test_classify_text_commodity_price_shock_english() -> None:
    assert classify_text("OPEC agrees to oil production cut of two million barrels")[0] == EventType.COMMODITY_PRICE_SHOCK
    assert classify_text("Wheat commodity price hits record high")[0] == EventType.COMMODITY_PRICE_SHOCK


def test_classify_text_economic_data_release_english() -> None:
    assert classify_text("US GDP contracts by 0.3 percent in Q1")[0] == EventType.ECONOMIC_DATA_RELEASE
    assert classify_text("PMI falls to 48 signalling contraction")[0] == EventType.ECONOMIC_DATA_RELEASE
    assert classify_text("Monthly jobs report beats expectations")[0] == EventType.ECONOMIC_DATA_RELEASE


def test_classify_text_economic_data_release_swedish() -> None:
    assert classify_text("Svensk BNP stiger mer än väntat")[0] == EventType.ECONOMIC_DATA_RELEASE


def test_classify_text_pandemic_outbreak_english() -> None:
    assert classify_text("WHO declares global pandemic emergency")[0] == EventType.PANDEMIC_OUTBREAK
    assert classify_text("Country enters full lockdown amid outbreak")[0] == EventType.PANDEMIC_OUTBREAK


def test_classify_text_energy_policy_english() -> None:
    assert classify_text("EU to impose carbon tax on heavy industry")[0] == EventType.ENERGY_POLICY
    assert classify_text("Government approves new nuclear power plant")[0] == EventType.ENERGY_POLICY


def test_new_types_gate2_compatible_with_themselves() -> None:
    for event_type in [
        EventType.CORPORATE_ACQUISITION,
        EventType.EXECUTIVE_CHANGE,
        EventType.REGULATORY_ACTION,
        EventType.DEBT_CRISIS,
        EventType.RESTRUCTURING,
        EventType.LEGAL_DISPUTE,
        EventType.PRODUCT_RECALL,
        EventType.DIVIDEND_CHANGE,
        EventType.CONTRACT_WIN,
        EventType.SHARE_BUYBACK,
        EventType.IPO_LISTING,
        EventType.CYBERSECURITY_INCIDENT,
        EventType.TRADE_POLICY,
        EventType.FISCAL_POLICY,
        EventType.CURRENCY_CRISIS,
        EventType.SOVEREIGN_DEBT,
        EventType.GEOPOLITICAL_TENSION,
        EventType.COMMODITY_PRICE_SHOCK,
        EventType.ECONOMIC_DATA_RELEASE,
        EventType.PANDEMIC_OUTBREAK,
        EventType.ENERGY_POLICY,
    ]:
        assert gate2_compatible(event_type, event_type), f"{event_type} should be gate2 compatible with itself"
