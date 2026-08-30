"""Rule-level regression tests for the E12 classification fixes.

``test_audit_replay.py`` asserts the end-to-end outcome for all 73 articles of the 2026-08-12 audit.
This module pins each individual rule, so a failure names the rule that broke rather than the
article
that noticed. Every test cites the article it was written from.

Kept separate from ``test_taxonomy.py`` because these tests share one subject — the precision fixes
CLN-63 - CLN-68 in ``requirements/SRS-03-cleansing.md`` §5 — and reading them together is how the
rules make sense.
"""

from __future__ import annotations

from shared.schemas.messages import AssetId, EventPolarity, EventType

from cleansing.taxonomy import (
    _MONEY_OR_PERCENT,
    EVENT_TYPE_ASSETS,
    EVENT_TYPE_GROUPS,
    FALLBACK_TARGET_FAMILY,
    NON_CLUSTERING_EVENT_TYPES,
    NON_FINANCIAL_EVENT_TYPES,
    NewsScope,
    classify_polarity,
    classify_text,
    cue_families_present,
    infer_assets,
    resolve_scope,
)

# --- CLN-63: the reject tier runs first ----------------------------------------------------------


def test_reject_tier_runs_before_the_specific_tier() -> None:
    # A52. "The War Raiders" is a wrestling tag team. The specific keyword "war" used to win before
    # the non-financial tier could reject it, and the match then moved gold and every weapons maker.
    event_type, _ = classify_text(
        "Damian Priest & R-Truth, The War Raiders and The MFTs clash in title bout",
        "Backed by WWE Hall of Famer Haku, Priest and R-Truth will defend the WWE Tag Team Title.",
    )
    assert event_type is EventType.SPORT


def test_company_headline_is_never_rejected_as_sport() -> None:
    # The escape hatch that has to survive the reorder: a registered company in the title means
    # financial news by definition, whatever sports word appears alongside.
    assert classify_text("Nike lifts full-year guidance", "")[0] is EventType.CORPORATE_EARNINGS


def test_body_reject_vetoes_a_market_classification_from_the_title() -> None:
    # A52 again, from the veto's side rather than the tier order's: the title alone yields
    # MILITARY_CONFLICT ("war" is a specific keyword, and no non-financial word appears in the
    # title), so only the body can reveal the subject.
    event_type, _ = classify_text(
        "The War Raiders clash in title bout",
        "Priest and R-Truth will defend the WWE Tag Team Title in a Triple Threat.",
    )
    assert event_type in NON_FINANCIAL_EVENT_TYPES


def test_sports_headline_with_no_market_keyword_resolves_to_nothing() -> None:
    # A70. The veto never runs here: the title yields no market keyword at all, so the type stays
    # OTHER. Asserted because "no prediction" is the outcome that matters, and OTHER now delivers
    # it.
    event_type, _ = classify_text(
        "Tactics Talk: Order and chaos in North Carolina vs. Washington",
        "The Courage average the most goals per game in the National Women's Soccer League.",
    )
    assert event_type in NON_CLUSTERING_EVENT_TYPES
    assert resolve_scope("Tactics Talk: Order and chaos", event_type).assets == ()


def test_body_reject_does_not_veto_a_company_headline() -> None:
    # The accepted-risk boundary: a real earnings story whose body mentions a sport must survive,
    # because the title names a registered company.
    event_type, _ = classify_text(
        "Exxon reports record revenue",
        "Football sponsorship spending rose, the basketball deal lapsed.",
    )
    assert event_type is EventType.CORPORATE_EARNINGS


# --- CLN-64: a generic keyword in a title needs corroboration ------------------------------------


def test_generic_closure_keyword_needs_corroboration() -> None:
    # A32. "Closure" of a garden display is not a strait closure.
    event_type, _ = classify_text(
        "Over 200 Mozart Figurines Stolen From Salzburg Art Installation, "
        "Forcing Early Closure Of Garden Display",
        "The Mozarteum Foundation confirmed that 210 of the 320 figures had disappeared.",
    )
    assert event_type is not EventType.STRAIT_CLOSURE


def test_generic_rate_keyword_is_not_a_rate_decision() -> None:
    # A35.
    event_type, _ = classify_text(
        "Stroller running linked to lower overuse injury rates for new parents",
        "Parents who ran with a stroller were less likely to sustain an overuse injury.",
    )
    assert event_type is not EventType.RATE_DECISION


def test_generic_keyword_is_corroborated_by_a_company() -> None:
    assert classify_text("Exxon halts output at Texas refinery", "")[0] is (
        EventType.SUPPLY_DISRUPTION
    )


def test_generic_keyword_is_corroborated_by_a_money_figure() -> None:
    # A20. "revenue" is generic and CoreWeave is not in the registry, so the money figure is the
    # only thing that makes this classifiable — and it is plainly an earnings headline.
    event_type, _ = classify_text("CoreWeave reports $2.575B Q2 revenue, $104B backlog", "")
    assert event_type is EventType.CORPORATE_EARNINGS


def test_a_bare_count_does_not_corroborate() -> None:
    # "Over 200 figurines" must not read as a market figure.
    assert not _MONEY_OR_PERCENT.search("Over 200 Mozart Figurines Stolen")
    assert _MONEY_OR_PERCENT.search("Profit falls 12% at the group")
    assert _MONEY_OR_PERCENT.search("Revenue reaches $2.575B")
    assert _MONEY_OR_PERCENT.search("Vinsten steg till 4,5 miljarder kr")


def test_generic_keyword_is_corroborated_by_an_institutional_term() -> None:
    assert classify_text("UK chancellor delivers annual budget", "")[0] is EventType.FISCAL_POLICY


def test_settings_default_is_not_a_debt_crisis() -> None:
    # A19. DEBT_CRISIS seeds a DOWN 0.55 edge to EVERY asset group (infra/neo4j/init/07), so one
    # false match moves the whole registry. This produced an AMZN DOWN MEDIUM from a Twitch settings
    # story.
    event_type, _ = classify_text(
        "Twitch Now Trains Amazon's Generative AI Models On Your Channel By Default",
        "The setting can be disabled, but it's turned on by default.",
    )
    assert event_type is not EventType.DEBT_CRISIS


def test_a_real_credit_event_is_still_a_debt_crisis() -> None:
    assert classify_text("Argentina defaults on sovereign debt", "")[0] is EventType.DEBT_CRISIS
    assert classify_text("Retailer files for bankruptcy", "")[0] is EventType.DEBT_CRISIS


# --- CLN-65: punctuation normalisation -----------------------------------------------------------


def test_company_survives_adjacent_punctuation() -> None:
    scope = resolve_scope("Exxon, Inc. lifts full-year guidance", EventType.CORPORATE_EARNINGS)
    assert scope.scope is NewsScope.COMPANY
    assert scope.assets == (AssetId.XOM_NYSE,)


def test_hyphenated_company_does_not_widen_to_its_industry() -> None:
    # The precedence inversion this fixes: the hyphen defeated the company match, the industry
    # keyword "missile" still matched, and a Lockheed headline predicted its competitors instead.
    scope = resolve_scope("Lockheed-Martin wins missile contract", EventType.CONTRACT_WIN)
    assert scope.scope is NewsScope.COMPANY
    assert scope.assets == (AssetId.LMT_NYSE,)


def test_quoted_company_is_recognised() -> None:
    assert infer_assets('"Newmont" beats estimates') == (AssetId.NEM_NYSE,)


def test_registry_keyword_containing_punctuation_still_matches() -> None:
    # "saab-b" is stored hyphenated in the registry; normalising both sides keeps it matchable.
    assert AssetId.SAAB_B_STO in infer_assets("Saab-B shares climb in Stockholm")


def test_swedish_letters_survive_normalisation() -> None:
    assert classify_text("Riksbanken höjer styrräntan med 25 punkter", "")[0] is (
        EventType.RATE_DECISION
    )


# --- CLN-66: the event-type fallback cue gate ----------------------------------------------------


def test_swedish_compound_registers_its_cue_stem() -> None:
    # A5. "luftkriget" (the air war) contains "krig" with no word boundary, so suffix tolerance
    # alone cannot see it. Without compound matching this justified prediction was lost.
    families = cue_families_present("Nytt ryskt drag – luftkriget mot Ukraina trappas upp")
    assert "SAFE_HAVEN" in families
    assert "DEFENCE" in families


def test_unrelated_swedish_headline_registers_no_cue_family() -> None:
    # A3. The eclipse feature whose BODY says "gjort slut på krig" — the cue check is title-only for
    # exactly this reason.
    assert cue_families_present("Kvällens solförmörkelse kan bli extra dramatisk") == frozenset()


def test_oilers_does_not_register_an_energy_cue() -> None:
    # A50. Bare "oil" is deliberately absent from the ENERGY cues: with suffix tolerance it also
    # fires on a hockey team.
    assert "ENERGY" not in cue_families_present("Edmonton Oilers have a logjam of goalies")


def test_every_fallback_target_has_a_cue_family() -> None:
    # Also enforced at import. Asserted here so the reason is documented: an unmapped target would
    # be silently excluded by the gate and would simply stop predicting, with no error anywhere.
    targets = {asset.value for assets in EVENT_TYPE_ASSETS.values() for asset in assets}
    targets |= {group for groups in EVENT_TYPE_GROUPS.values() for group in groups}
    assert targets <= set(FALLBACK_TARGET_FAMILY)


def test_commodity_shock_reaches_only_the_matching_commodity() -> None:
    # A22. The gold forecast that produced nothing at all before, because COMMODITY_PRICE_SHOCK had
    # no fallback entry — and which must not reach oil now that it has one.
    gold = resolve_scope("Gold expected to trade around $4,500/oz", EventType.COMMODITY_PRICE_SHOCK)
    assert AssetId.NEM_NYSE in gold.assets
    assert AssetId.XOM_NYSE not in gold.assets

    oil = resolve_scope("Crude slides after OPEC raises output", EventType.COMMODITY_PRICE_SHOCK)
    assert AssetId.XOM_NYSE in oil.assets
    assert AssetId.NEM_NYSE not in oil.assets


def test_other_event_type_resolves_to_nothing() -> None:
    # A6/A16. An OTHER event has no causal factor, so attaching assets to it only created contexts
    # that could never predict. A private ice-cream press release attached AAK through the "food"
    # keyword.
    scope = resolve_scope(
        "UNITED DAIRY FARMERS, INC. AND GRIPPO FOODS, INC. TEAM UP FOR ICE CREAM COLLABORATION",
        EventType.OTHER,
    )
    assert scope.scope is NewsScope.NONE
    assert scope.assets == ()


# --- CLN-67: scope provenance --------------------------------------------------------------------


def test_fallback_provenance_names_the_cue_family() -> None:
    scope = resolve_scope("Gold holds near record as war fears mount", EventType.MILITARY_CONFLICT)
    assert scope.scope is NewsScope.EVENT_TYPE
    assert "MILITARY_CONFLICT" in scope.matched
    assert "SAFE_HAVEN" in scope.matched


def test_every_non_empty_scope_records_why() -> None:
    for text, event_type in [
        ("Tesla acquired by rival firm", EventType.CORPORATE_EARNINGS),
        ("Defence spending rises across NATO", EventType.FISCAL_POLICY),
        ("Gold holds near record as war fears mount", EventType.MILITARY_CONFLICT),
    ]:
        scope = resolve_scope(text, event_type)
        assert scope.assets
        assert scope.matched, f"{text!r} selected assets without recording why"


# --- CLN-68: REGULATORY_ACTION conflates approval and enforcement --------------------------------
#
# The factor carries one DOWN prior (enforcement is the more common case), so an approval predicted
# a fall. An approval is reported as RESOLUTION instead, which negates the factor's stored sign at
# decision time.


def test_a_regulatory_approval_is_reported_as_resolution() -> None:
    event_type, _ = classify_text("FDA approves AstraZeneca's new drug", "")
    assert event_type is EventType.REGULATORY_ACTION
    assert classify_polarity("FDA approves AstraZeneca's new drug", event_type) is (
        EventPolarity.RESOLUTION
    )


def test_regulatory_enforcement_keeps_the_stored_sign() -> None:
    for title in (
        "AstraZeneca fined by the regulator",
        "Regulator rejects Moderna's application",
        "Novo Nordisk licence revoked",
    ):
        event_type, _ = classify_text(title, "")
        assert classify_polarity(title, event_type) is EventPolarity.OCCURRENCE, title


def test_enforcement_wins_when_both_readings_are_present() -> None:
    # "approval withdrawn after record fine" is not good news; the conservative reading keeps DOWN.
    title = "AstraZeneca approval withdrawn after record fine"
    event_type, _ = classify_text(title, "")
    assert classify_polarity(title, event_type) is EventPolarity.OCCURRENCE


def test_swedish_regulatory_approval_is_recognised() -> None:
    title = "Läkemedelsverket godkänner nytt läkemedel från AstraZeneca"
    event_type, _ = classify_text(title, "")
    assert event_type is EventType.REGULATORY_ACTION
    assert classify_polarity(title, event_type) is EventPolarity.RESOLUTION


def test_approval_inversion_does_not_leak_to_other_factors() -> None:
    # The reason these cues cannot go in the global RESOLUTION_CUES: a merger approval would flip
    # CORPORATE_ACQUISITION's UP prior to DOWN.
    title = "Merger approved by the competition authority"
    event_type, _ = classify_text(title, "")
    assert event_type is EventType.CORPORATE_ACQUISITION
    assert classify_polarity(title, event_type) is EventPolarity.OCCURRENCE


def test_polarity_without_an_event_type_keeps_global_behaviour() -> None:
    # The parameter is optional; omitting it must not change the de-escalation path.
    assert classify_polarity("USA calls off Iran attack") is EventPolarity.RESOLUTION
    assert classify_polarity("Russia escalates strikes") is EventPolarity.OCCURRENCE
