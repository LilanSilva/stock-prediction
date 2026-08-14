from __future__ import annotations

import pytest
from shared.reference import members_of
from shared.schemas.messages import AssetId, ConditionCode, EventPolarity, EventType

from cleansing.extraction import KeywordExtractor, SpacyExtractor, build_extractor


async def test_keyword_extractor_has_no_actor_and_classifies() -> None:
    extractor = KeywordExtractor()
    assert extractor.is_ready() is True
    action = await extractor.extract("EU imposes sanctions on Russian oil exports", "en")
    # The deterministic backend never guesses participants; that is reserved for spaCy/LLM.
    assert action.actor is None
    assert action.object is None
    assert action.event_type == EventType.SANCTIONS


async def test_keyword_extractor_returns_other_without_a_keyword() -> None:
    action = await KeywordExtractor().extract("A quiet day with nothing notable", "en")
    assert action.event_type == EventType.OTHER
    assert action.actor is None


async def test_keyword_extractor_populates_polarity_and_context_tags() -> None:
    action = await KeywordExtractor().extract("Tanker blocked in Strait of Hormuz", "en")
    assert action.event_type == EventType.STRAIT_CLOSURE
    assert action.polarity == EventPolarity.OCCURRENCE
    assert ConditionCode.TRANSPORT_AFFECTED in action.context_tags


async def test_keyword_extractor_detects_resolution() -> None:
    action = await KeywordExtractor().extract("USA calls off Iran attack", "en")
    assert action.event_type == EventType.MILITARY_CONFLICT
    assert action.polarity == EventPolarity.RESOLUTION
    # No company or industry keyword is named, so assets fall back to the event type's graph assets
    # — the gold proxy plus the weapons makers a conflict moves.
    assert AssetId.NEM_NYSE in action.affected_asset_ids
    assert set(members_of("WEAPON_INDUSTRY")) <= set(action.affected_asset_ids)
    # A distant conflict with no transport cue is a safe-haven tag, and the asset scope now agrees
    # with it: the oil proxy is excluded (E12 CLN-66). These two used to contradict each other — the
    # tag said "gold only" while the scope attached oil anyway.
    assert AssetId.XOM_NYSE not in action.affected_asset_ids
    assert action.context_tags == (ConditionCode.SAFE_HAVEN_ONLY,)


async def test_spacy_extractor_falls_back_when_no_pipeline_loaded() -> None:
    # Exercises the no-model fallback branch without requiring the spaCy models to be installed.
    extractor = SpacyExtractor()
    assert extractor.is_ready() is False
    action = await extractor.extract("EU imposes sanctions on oil exports", "en")
    assert action.actor is None
    assert action.event_type == EventType.SANCTIONS


def test_build_extractor_variants() -> None:
    assert isinstance(build_extractor("keyword"), KeywordExtractor)
    assert isinstance(build_extractor("KEYWORD"), KeywordExtractor)
    assert isinstance(build_extractor("spacy"), SpacyExtractor)


def test_build_extractor_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown nlp backend"):
        build_extractor("nope")
