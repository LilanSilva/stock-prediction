from __future__ import annotations

import pytest
from shared.schemas.messages import EventType

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
