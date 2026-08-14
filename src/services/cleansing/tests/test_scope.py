"""Tests for company-vs-industry news scope and industry fan-out.

The two scenarios this must get right:
  - "Tesla acquired by rival"  -> company scope, Tesla alone.
  - "War begins"               -> industry scope, every weapons maker in every market.
"""

from __future__ import annotations

from shared.reference import group_of, members_of
from shared.schemas.messages import AssetId, EventType

from cleansing.taxonomy import (
    NewsScope,
    assets_for_event_type,
    infer_assets,
    resolve_scope,
)

# --- company scope ------------------------------------------------------------------------------


def test_company_news_moves_only_that_company() -> None:
    scope = resolve_scope("Tesla acquired by rival firm", EventType.CORPORATE_EARNINGS)
    assert scope.scope is NewsScope.COMPANY
    assert scope.assets == (AssetId.TSLA_NASDAQ,)
    assert "tesla" in scope.matched


def test_company_scope_does_not_widen_to_its_industry() -> None:
    # The whole point of company scope: a Tesla-specific story must not move other carmakers.
    scope = resolve_scope("Tesla acquired by rival firm", EventType.CORPORATE_EARNINGS)
    siblings = set(members_of(group_of(AssetId.TSLA_NASDAQ))) - {AssetId.TSLA_NASDAQ}
    assert not siblings & set(scope.assets)


def test_company_keyword_beats_an_industry_keyword_in_the_same_text() -> None:
    # "Saab" (company) and "fighter jet" (industry) both appear; the specific match must win.
    scope = resolve_scope("Saab wins fighter jet contract", EventType.CORPORATE_EARNINGS)
    assert scope.scope is NewsScope.COMPANY
    assert scope.assets == (AssetId.SAAB_B_STO,)


def test_non_us_company_is_recognised() -> None:
    scope = resolve_scope("Novo Nordisk raises Ozempic guidance", EventType.CORPORATE_EARNINGS)
    assert scope.scope is NewsScope.COMPANY
    assert scope.assets == (AssetId.NOVO_B_CPH,)


# --- industry scope ----------------------------------------------------------------------------


def test_industry_news_fans_out_to_every_member_across_markets() -> None:
    # FISCAL_POLICY, not OTHER: an OTHER event has no causal factor and so resolves to no assets at
    # all (E12 CLN-66). This test only ever used OTHER as a "don't care" placeholder.
    scope = resolve_scope("Defence spending rises across NATO", EventType.FISCAL_POLICY)
    assert scope.scope is NewsScope.INDUSTRY
    assert set(scope.assets) == set(members_of("WEAPON_INDUSTRY"))
    # Fan-out must cross markets, not just hit the US listing.
    assert AssetId.LMT_NYSE in scope.assets
    assert AssetId.SAAB_B_STO in scope.assets


def test_industry_fan_out_spans_multiple_currencies() -> None:
    scope = resolve_scope("Semiconductor shortage worsens", EventType.SUPPLY_DISRUPTION)
    assert scope.scope is NewsScope.INDUSTRY
    assert set(scope.assets) == set(members_of("SEMICONDUCTOR_INDUSTRY"))


def test_plural_headline_still_matches_a_singular_keyword() -> None:
    # Headlines pluralise; the registry stores singular forms.
    scope = resolve_scope("Missiles strike the airbase", EventType.MILITARY_CONFLICT)
    assert scope.scope is NewsScope.INDUSTRY
    assert AssetId.SAAB_B_STO in scope.assets


# --- event-type fallback -----------------------------------------------------------------------


def test_falls_back_to_event_type_assets_when_nothing_is_named() -> None:
    scope = resolve_scope("USA calls off Iran attack", EventType.MILITARY_CONFLICT)
    assert scope.scope is NewsScope.EVENT_TYPE
    assert AssetId.NEM_NYSE in scope.assets
    # The oil proxy is NOT selected. A conflict with no transport cue is a safe-haven bid for gold
    # and nothing more — the same rule ADR-006's conditioned edges apply at the edge layer, now
    # applied at the asset-selection layer too (E12 CLN-66). Before this the two layers disagreed:
    # infer_conditions tagged the event SAFE_HAVEN_ONLY while scope resolution still attached the
    # oil proxy, which then propagated a contradictory stance back onto gold.
    assert AssetId.XOM_NYSE not in scope.assets


def test_conflict_with_a_transport_cue_does_reach_the_oil_proxy() -> None:
    # The other half of the rule above: once the headline threatens oil logistics, oil is in scope.
    scope = resolve_scope("Missiles strike an oil refinery", EventType.MILITARY_CONFLICT)
    assert AssetId.XOM_NYSE in scope.assets


def test_fallback_requires_a_domain_cue_not_just_the_event_type() -> None:
    # The defect this gate closes: a stolen-figurines story classified as STRAIT_CLOSURE and moved
    # oil, because the event type alone was treated as evidence that the article was about a strait.
    scope = resolve_scope(
        "Over 200 Mozart Figurines Stolen From Salzburg Art Installation, "
        "Forcing Early Closure Of Garden Display",
        EventType.STRAIT_CLOSURE,
    )
    assert scope.scope is NewsScope.NONE
    assert scope.assets == ()


def test_fallback_records_the_cue_family_that_admitted_the_assets() -> None:
    scope = resolve_scope("Gold holds near record as war fears mount", EventType.MILITARY_CONFLICT)
    assert scope.scope is NewsScope.EVENT_TYPE
    assert "SAFE_HAVEN" in scope.matched
    assert "MILITARY_CONFLICT" in scope.matched


def test_military_conflict_fallback_also_reaches_weapons_makers() -> None:
    # A war moves defence stocks, not only the safe-haven commodities.
    assets = assets_for_event_type(EventType.MILITARY_CONFLICT)
    assert AssetId.NEM_NYSE in assets
    assert set(members_of("WEAPON_INDUSTRY")) <= set(assets)


def test_unmatched_text_resolves_to_nothing() -> None:
    scope = resolve_scope("Local library extends opening hours", EventType.OTHER)
    assert scope.scope is NewsScope.NONE
    assert scope.assets == ()


# --- invariants --------------------------------------------------------------------------------


def test_resolved_assets_are_unique() -> None:
    # Overlapping industry keywords must not list an asset twice: a duplicate would double-count
    # the same instrument in one context.
    for text, event_type in [
        ("Defence and aerospace budgets climb", EventType.FISCAL_POLICY),
        ("Missiles strike an oil refinery", EventType.MILITARY_CONFLICT),
        ("USA calls off Iran attack", EventType.MILITARY_CONFLICT),
    ]:
        assets = resolve_scope(text, event_type).assets
        assert len(assets) == len(set(assets))


def test_every_resolved_asset_is_in_the_registry() -> None:
    for text, event_type in [
        ("Tesla acquired", EventType.CORPORATE_EARNINGS),
        ("Defence spending rises", EventType.FISCAL_POLICY),
        ("USA calls off Iran attack", EventType.MILITARY_CONFLICT),
    ]:
        for asset in resolve_scope(text, event_type).assets:
            assert group_of(asset)


def test_infer_assets_stays_company_only() -> None:
    # infer_assets is the narrow lookup; it must not fan out or fall back.
    assert infer_assets("Tesla acquired") == (AssetId.TSLA_NASDAQ,)
    assert infer_assets("Defence spending rises") == ()
