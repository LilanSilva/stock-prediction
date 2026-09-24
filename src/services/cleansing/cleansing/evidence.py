"""Bounded, inspectable evidence selection and narrow factuality guards."""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.schemas.messages import EventType
from shared.text import assess_text, sentences

CLASSIFIER_VERSION = "quality-tiers-v1"


def prepare_inputs(title: str, body: str, mode: str = "title_first") -> tuple[str, str]:
    if mode not in {"title_first", "title_only", "title_with_relevant_context"}:
        raise ValueError(f"unknown classification mode: {mode}")
    title = assess_text(title, title=True).usable
    body = assess_text(body or "").usable if mode != "title_only" else ""
    if mode == "title_with_relevant_context":
        body = relevant_body(title, body)
    return title, body


@dataclass(frozen=True)
class ClassificationDecision:
    event_type: EventType
    keyword: str | None
    source: str
    reason: str
    evidence: str
    allow_llm: bool = False


def safety_reason(title: str) -> str | None:
    """Do not mistake advice, reader-service tools or a retracted threat for an occurrence.

    Deliberately not a blanket ban on forecasts, plans, all opinion columns, or the word 'may'.
    """
    text = title.casefold()
    if re.search(r"\b(?:bör|borde|should|ought to)\s+(?:avgå|resign|step down)\b", text):
        return "resignation_opinion"
    if re.search(r"\b(?:så nära bor du|how close (?:do )?you live|find your nearest)\b", text):
        return "reader_service"
    if re.search(r"\b(?:skämtade|skämtat|joked|joking)\b", text) and re.search(
        r"\b(?:hormuz\w*|military|attack\w*|invasion|anfall\w*)\b", text
    ):
        return "reported_joke"
    if re.search(
        r"\b(?:denies?|denied|förnekar|nekar till)\b.{0,50}\b"
        r"(?:attack\w*|invasion|anfall\w*|acquisition|förvärv|ceasefire|vapenvila)\b",
        text,
    ):
        return "denied_event"
    return None


_STOPWORDS = frozenset(
    "the and for with after before this that from says said news update announces announced "
    "company firm major latest today report reports som och att för med efter före från "
    "det den till har ett om på av nyheter säger bolaget besked presenterar".split()
)


def relevant_body(title: str, body: str) -> str:
    """Use sentences anchored to a headline term; no arbitrary first-N-sentences truncation."""
    terms = set(re.findall(r"\b\w{3,}\b", title.casefold())) - _STOPWORDS
    return "\n".join(
        span
        for span in sentences(body)
        if terms.intersection(re.findall(r"\b\w{3,}\b", span.casefold()))
    )
