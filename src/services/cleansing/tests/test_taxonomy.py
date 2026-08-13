from __future__ import annotations

from shared.reference import members_of
from shared.schemas.messages import AssetId, ConditionCode, EventPolarity, EventType

from cleansing.taxonomy import (
    NewsScope,
    assets_for_event_type,
    classify_polarity,
    classify_text,
    gate2_compatible,
    infer_assets,
    infer_conditions,
    map_action,
    resolve_scope,
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


def test_infer_assets_matches_company_keywords_only() -> None:
    # infer_assets is the narrow COMPANY-scope lookup: it resolves a listing named specifically in
    # the text and nothing else. (The commodity cases this test used to cover went away with the
    # GOLD/BRENT_OIL instruments; company keywords are what the registry declares now.)
    assert infer_assets("Exxon lifts guidance after refinery upgrade") == (AssetId.XOM_NYSE,)
    assert infer_assets("Newmont reports higher output") == (AssetId.NEM_NYSE,)
    assert set(infer_assets("Exxon and Newmont both rally")) == {
        AssetId.NEM_NYSE,
        AssetId.XOM_NYSE,
    }
    # An industry-level headline names no company, so company scope resolves nothing.
    assert infer_assets("oil prices climb on OPEC news") == ()


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
    assert {AssetId.NEM_NYSE, AssetId.XOM_NYSE} <= set(conflict)
    assert set(members_of("WEAPON_INDUSTRY")) <= set(conflict)

    # A supply disruption moves oil and the producers/refiners, not defence.
    disruption = assets_for_event_type(EventType.SUPPLY_DISRUPTION)
    assert AssetId.XOM_NYSE in disruption
    assert set(members_of("OIL_GAS")) <= set(disruption)
    assert AssetId.SAAB_B_STO not in disruption

    # An event type with no group mapping stays commodity-only.
    assert assets_for_event_type(EventType.INFLATION_CHANGE) == (AssetId.NEM_NYSE,)


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


# --- Non-financial reject bucket -------------------------------------------------------------
#
# These are real headlines from a day of production output that the classifier previously typed as
# market events, because generic keywords ("close", "gold", "penalty", "contract") matched. An
# asset-bearing type here becomes a prediction and a notification, so each must stay non-financial.


def test_sport_headlines_are_not_market_events() -> None:
    for title in [
        "Which Florida Panthers Are Close to Going to the Hockey Hall of Fame",
        "Josh Berry Is Racing for His NASCAR Career",
        "Elena Rybakina beats Coco Gauff to reach Canadian Open tennis final",
        "UAA men's basketball schedule loaded with minefields on the road",
    ]:
        assert classify_text(title)[0] == EventType.SPORT, title


def test_entertainment_and_lifestyle_headlines() -> None:
    assert classify_text("Malmö named Eurovision host city")[0] == EventType.ENTERTAINMENT
    assert classify_text("Your Daily Horoscope For Every Star Sign")[0] == EventType.LIFESTYLE


def test_non_financial_types_never_cluster() -> None:
    # Same reasoning as OTHER: no causal event, so a cluster of them could never be predicted on.
    for event_type in [EventType.SPORT, EventType.ENTERTAINMENT, EventType.LIFESTYLE]:
        assert not gate2_compatible(event_type, event_type)


def test_non_financial_types_resolve_no_assets() -> None:
    # Even when the text names a listed company, a non-financial event moves nothing. Without this
    # the event-type fallback would hand a football result the gold and oil proxies.
    scope = resolve_scope("Exxon employees win the company golf tournament", EventType.SPORT)
    assert scope.scope is NewsScope.NONE
    assert scope.assets == ()
    assert assets_for_event_type(EventType.SPORT) == ()


def test_company_headline_is_never_rejected_as_non_financial() -> None:
    # A registered company in the headline means financial news, even with sport/entertainment words
    # nearby. Suppressing these would be a far more expensive error than mistyping a match report.
    assert classify_text("Exxon signs supply contract with the NFL")[0] != EventType.SPORT
    assert classify_text("Newmont lifts guidance after golf-course land sale")[0] == (
        EventType.CORPORATE_EARNINGS
    )


def test_generic_word_does_not_reject_market_news() -> None:
    # "game" is deliberately absent from NON_FINANCIAL_KEYWORDS: it occurs in market copy too.
    event_type, _ = classify_text(
        "Goldman Sachs is paying $2.25 billion to get into Bitcoin income game"
    )
    assert event_type not in {EventType.SPORT, EventType.ENTERTAINMENT, EventType.LIFESTYLE}


# --- Evidence tiering ------------------------------------------------------------------------


def test_specific_keyword_in_title_beats_generic_one() -> None:
    # "close" appears before "hike" but is generic, so the rate decision must win regardless of
    # position. Position within the text is not a measure of relevance.
    event_type, keyword = classify_text(
        "US Open: S&P 500 close to highs as September Fed hike chances fall"
    )
    assert event_type == EventType.RATE_DECISION
    assert keyword == "hike"


def test_specific_keyword_in_body_recovers_vague_headline() -> None:
    event_type, _ = classify_text(
        "Bolaget kommenterar kvartalet",
        "Rörelseresultatet föll till 120 miljoner kronor jämfört med föregående år.",
    )
    assert event_type == EventType.CORPORATE_EARNINGS


def test_generic_keyword_in_body_is_ignored() -> None:
    # The regression that shipped: a sports report whose body says "close" was typed STRAIT_CLOSURE.
    event_type, _ = classify_text(
        "Mets fall to Atlanta in extra innings",
        "The game was close throughout, with gold-glove defence and a penalty-free ninth.",
    )
    assert event_type not in {EventType.STRAIT_CLOSURE, EventType.COMMODITY_PRICE_SHOCK}


# --- Swedish recall --------------------------------------------------------------------------


def test_swedish_earnings_compounds() -> None:
    for title in [
        "Vinstkross från Embracer",
        "Raysearchs siffror i linje med vinstvarningen",
        "Vinstkollaps för gruvjätten",
        "Thyssenkrupp skruvar upp golvet för vinstutsikterna",
    ]:
        assert classify_text(title)[0] == EventType.CORPORATE_EARNINGS, title


def test_swedish_rate_and_acquisition_compounds() -> None:
    assert classify_text("Räntebeskedet från Norges Bank")[0] == EventType.RATE_DECISION
    assert classify_text("Kenneth Dart lägger budpliktsbud på Evolution")[0] == (
        EventType.CORPORATE_ACQUISITION
    )


def test_gold_medal_does_not_resolve_to_gold_miners() -> None:
    # `gold` was an industry keyword on PRECIOUS_METALS, so any "gold medal" story fanned out to
    # every gold miner in the group — 18 identical wrong LUG_STO predictions in one audited day.
    scope = resolve_scope(
        "Indian fencing team secures five more gold medals at the Commonwealth Games",
        EventType.OTHER,
    )
    assert scope.assets == ()


def test_gold_price_still_resolves_to_gold_miners() -> None:
    # Narrowing the keyword must not cost real commodity coverage.
    scope = resolve_scope(
        "Gold price expected to trade around $4,500/oz by end of 2026",
        EventType.COMMODITY_PRICE_SHOCK,
    )
    assert scope.scope is NewsScope.INDUSTRY
    assert AssetId.LUG_STO in scope.assets


def test_bare_swedish_vinst_is_not_earnings() -> None:
    # "vinst" alone means "a win" in Swedish sports reporting, so it is deliberately not a keyword.
    # Guarding this stops the fix from recreating the bug class it was written to remove.
    event_type, _ = classify_text("Djurgårdens vinst mot AIK i matchen igår")
    assert event_type != EventType.CORPORATE_EARNINGS
