"""Action-signature extraction backends.

`ActionExtractor` produces the actor/action/object triple and the canonical event type used for
Gate 2 clustering and local event construction. Two backends are provided:

  - `KeywordExtractor` (default): deterministic taxonomy keyword scan plus a light actor/object
    heuristic. No external model, so the walking skeleton and unit tests run everywhere.
  - `SpacyExtractor`: real per-language spaCy pipelines (en_core_web_sm / sv_core_news_sm), loaded
    lazily. Lemmas are mapped to the canonical taxonomy locally, never translated by an LLM
    (functional document sec 5).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shared.schemas.messages import EventType

from cleansing.classify import LlmClassifier
from cleansing.models import ExtractedAction
from cleansing.taxonomy import (
    classify_polarity,
    classify_text,
    infer_conditions,
    map_action,
    resolve_scope,
)


@runtime_checkable
class ActionExtractor(Protocol):
    """Stable extraction interface (title + language [+ body, url] -> canonical ExtractedAction).

    ``body`` is optional and used only as weaker, second-choice evidence for the event type (see
    ``taxonomy.classify_text``). Polarity, context tags and asset scope are always derived from the
    title alone, because body prose routinely mentions unrelated events as background.

    ``url`` is optional and contributes only the publisher-section signal (CLN-71): it can move an
    article into a non-financial type but never selects an asset, sets a polarity or infers a
    condition. Both extra arguments default to empty so a caller with only a headline still works.
    """

    def is_ready(self) -> bool: ...

    async def extract(
        self, title: str, language: str, body: str = "", url: str = ""
    ) -> ExtractedAction: ...


def _build_action(
    text: str,
    event_type: EventType,
    *,
    actor: str | None,
    action_lemma: str | None,
    obj: str | None,
    original_lemma: str | None,
) -> ExtractedAction:
    """Assemble an ExtractedAction, enriching it with polarity, context tags, and assets.

    Asset selection is scope-aware (see ``taxonomy.resolve_scope``): a company-specific headline
    moves only that company, an industry headline fans out to every listing in the group across
    markets, and anything else falls back to the event type's graph assets.
    """
    scope = resolve_scope(text, event_type)
    return ExtractedAction(
        actor=actor,
        action_lemma=action_lemma,
        object=obj,
        original_lemma=original_lemma,
        event_type=event_type,
        affected_asset_ids=scope.assets,
        polarity=classify_polarity(text, event_type),
        context_tags=tuple(infer_conditions(text, event_type)),
    )


class KeywordExtractor:
    """Deterministic taxonomy-keyword extractor (POC default, no external model).

    ``llm_classifier`` is consulted only when ``classify_text`` returns OTHER — never for an article
    the deterministic tiers already resolved, and never more than once per article. See
    ``cleansing.classify`` for why: OTHER already means "no keyword fit", so this is the one case an
    LLM call can improve on without second-guessing a confident local answer.
    """

    def __init__(self, llm_classifier: LlmClassifier | None = None) -> None:
        self._llm_classifier = llm_classifier

    def is_ready(self) -> bool:
        return True

    async def extract(
        self, title: str, language: str, body: str = "", url: str = ""
    ) -> ExtractedAction:
        event_type, keyword = classify_text(title, body, url)
        if event_type is EventType.OTHER and self._llm_classifier is not None:
            event_type = await self._llm_classifier.classify(title, body, url)
        return _build_action(
            title,
            event_type,
            actor=None,
            action_lemma=keyword,
            obj=None,
            original_lemma=keyword,
        )


class SpacyExtractor:
    """Real spaCy backend with per-language pipelines, imported lazily."""

    def __init__(self, llm_classifier: LlmClassifier | None = None) -> None:
        self._llm_classifier = llm_classifier
        self._pipelines: dict[str, object] = {}
        self._model_by_language = {
            "en": "en_core_web_sm",
            "sv": "sv_core_news_sm",
        }
        self._loaded = False

    def is_ready(self) -> bool:
        return self._loaded

    def load(self) -> None:
        """Load the configured per-language models (called at startup for readiness)."""
        try:
            import spacy
        except ImportError as exc:  # pragma: no cover - only without the `ml` extra
            raise ImportError(
                "spacy not installed; install the 'ml' extra to use SpacyExtractor"
            ) from exc
        for language, model_name in self._model_by_language.items():
            self._pipelines[language] = spacy.load(model_name)
        self._loaded = True

    async def extract(
        self, title: str, language: str, body: str = "", url: str = ""
    ) -> ExtractedAction:
        import asyncio

        nlp = self._pipelines.get(language) or self._pipelines.get("en")
        if nlp is None:
            # Fall back to the deterministic classifier if no pipeline is loaded.
            event_type, keyword = classify_text(title, body, url)
            if event_type is EventType.OTHER and self._llm_classifier is not None:
                event_type = await self._llm_classifier.classify(title, body, url)
            return _build_action(
                title,
                event_type,
                actor=None,
                action_lemma=keyword,
                obj=None,
                original_lemma=keyword,
            )

        def _parse() -> tuple[str | None, str | None, str | None, EventType]:
            doc = nlp(title)  # type: ignore[operator]
            actor: str | None = None
            action_lemma: str | None = None
            obj: str | None = None
            for token in doc:
                if token.dep_ in {"nsubj", "nsubjpass"} and actor is None:
                    actor = token.text
                elif token.pos_ == "VERB" and action_lemma is None:
                    action_lemma = token.lemma_.lower()
                elif token.dep_ in {"dobj", "obj", "pobj"} and obj is None:
                    obj = token.text
            mapped = map_action(action_lemma)
            if mapped == EventType.OTHER:
                # The headline verb was not in the taxonomy. Back off to the tiered keyword scan,
                # which may still recognise the event from the publisher section, the title, or
                # (specific keywords only) the body.
                mapped, keyword = classify_text(title, body, url)
                action_lemma = action_lemma or keyword
            return actor, action_lemma, obj, mapped

        # spaCy parsing is synchronous CPU work, run off the event loop; the LLM fallback below is
        # network I/O and must run back on it, so the two cannot share one to_thread call.
        actor, action_lemma, obj, mapped = await asyncio.to_thread(_parse)
        if mapped is EventType.OTHER and self._llm_classifier is not None:
            mapped = await self._llm_classifier.classify(title, body, url)
        return _build_action(
            title,
            mapped,
            actor=actor,
            action_lemma=action_lemma,
            obj=obj,
            original_lemma=action_lemma,
        )


def build_extractor(backend: str, llm_classifier: LlmClassifier | None = None) -> ActionExtractor:
    """Construct the configured extraction backend."""
    normalized = backend.strip().lower()
    if normalized == "keyword":
        return KeywordExtractor(llm_classifier)
    if normalized == "spacy":
        return SpacyExtractor(llm_classifier)
    raise ValueError(f"unknown nlp backend: {backend!r}")
