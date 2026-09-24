from __future__ import annotations

from typing import cast

import pytest
from cleansing.classify import LlmClassifier
from cleansing.extraction import KeywordExtractor, SpacyExtractor, build_extractor
from shared.reference import members_of
from shared.schemas.messages import AssetId, ConditionCode, EventPolarity, EventType


class _StubClassifier:
    """Records whether it was called and returns a fixed type — no gateway, no network."""

    def __init__(self, event_type: EventType = EventType.LEGAL_DISPUTE) -> None:
        self._event_type = event_type
        self.calls = 0

    async def classify(self, title: str, body: str = "", url: str = "") -> EventType:
        self.calls += 1
        return self._event_type


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


async def test_keyword_extractor_calls_llm_only_when_other() -> None:
    stub = _StubClassifier(EventType.LEGAL_DISPUTE)
    action = await KeywordExtractor(cast(LlmClassifier, stub)).extract(
        "A quiet day with nothing notable", "en"
    )
    assert stub.calls == 1
    assert action.event_type == EventType.LEGAL_DISPUTE


async def test_keyword_extractor_does_not_call_llm_when_already_classified() -> None:
    # Already-classified articles must never reach the LLM, even if one is configured.
    stub = _StubClassifier(EventType.LEGAL_DISPUTE)
    action = await KeywordExtractor(cast(LlmClassifier, stub)).extract(
        "EU imposes sanctions on Russian oil exports", "en"
    )
    assert stub.calls == 0
    assert action.event_type == EventType.SANCTIONS


async def test_keyword_extractor_without_classifier_stays_other() -> None:
    action = await KeywordExtractor(None).extract("A quiet day with nothing notable", "en")
    assert action.event_type == EventType.OTHER


async def test_spacy_extractor_falls_back_when_no_pipeline_loaded() -> None:
    # Exercises the no-model fallback branch without requiring the spaCy models to be installed.
    extractor = SpacyExtractor()
    assert extractor.is_ready() is False
    action = await extractor.extract("EU imposes sanctions on oil exports", "en")
    assert action.actor is None
    assert action.event_type == EventType.SANCTIONS


async def test_spacy_extractor_without_pipeline_uses_llm_fallback_on_other() -> None:
    stub = _StubClassifier(EventType.LEGAL_DISPUTE)
    action = await SpacyExtractor(cast(LlmClassifier, stub)).extract(
        "A quiet day with nothing notable", "en"
    )
    assert stub.calls == 1
    assert action.event_type == EventType.LEGAL_DISPUTE


def test_build_extractor_variants() -> None:
    assert isinstance(build_extractor("keyword"), KeywordExtractor)
    assert isinstance(build_extractor("KEYWORD"), KeywordExtractor)
    assert isinstance(build_extractor("spacy"), SpacyExtractor)


def test_build_extractor_threads_the_llm_classifier_through() -> None:
    stub = cast(LlmClassifier, _StubClassifier())
    keyword_extractor = build_extractor("keyword", stub)
    assert isinstance(keyword_extractor, KeywordExtractor)
    assert keyword_extractor._llm_classifier is stub  # noqa: SLF001 - wiring check


def test_build_extractor_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown nlp backend"):
        build_extractor("nope")


@pytest.mark.parametrize("backend", [KeywordExtractor, SpacyExtractor])
async def test_safety_decision_cannot_reach_llm_or_spacy_shortcut(
    backend: type[KeywordExtractor] | type[SpacyExtractor],
) -> None:
    stub = _StubClassifier(EventType.MILITARY_CONFLICT)
    extractor = backend(cast(LlmClassifier, stub))
    if isinstance(extractor, SpacyExtractor):
        extractor._pipelines["en"] = lambda _: pytest.fail("unsafe input reached spaCy")
    action = await extractor.extract("Union leaders should resign", "en", "Attack claim.")
    assert action.event_type == EventType.OTHER
    assert action.affected_asset_ids == ()
    assert action.classification_audit["reason"] == "resignation_opinion"
    assert stub.calls == 0


async def test_title_only_mode_does_not_send_body_to_llm() -> None:
    from unittest.mock import AsyncMock

    classifier = AsyncMock(spec=LlmClassifier)
    classifier.classify.return_value = EventType.OTHER
    extractor = KeywordExtractor(classifier, classification_mode="title_only")
    await extractor.extract("A quiet day", "en", "Company announces earnings")
    assert classifier.classify.call_args.args[1] == ""


@pytest.mark.parametrize(
    "title,disagrees", [("Leaders impose sanctions", False), ("Leaders report earnings", True)]
)
async def test_spacy_audit_records_actual_source_and_tier_disagreement(
    title: str, disagrees: bool
) -> None:
    from types import SimpleNamespace

    extractor = SpacyExtractor()
    extractor._pipelines["en"] = lambda _: [
        SimpleNamespace(dep_="", pos_="VERB", lemma_="sanction", text="sanction")
    ]
    action = await extractor.extract(title, "en")
    assert action.event_type == EventType.SANCTIONS
    assert action.classification_audit["source"] == "spacy_lemma"
    assert action.classification_audit["evidence"] == title
    assert action.classification_audit["backend_disagreement"] is disagrees
