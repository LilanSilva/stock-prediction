from __future__ import annotations

import pytest
from cleansing.config import CleansingSettings
from cleansing.taxonomy import classify_decision, classify_polarity
from pydantic import ValidationError
from shared.schemas.messages import EventPolarity, EventType


def test_quality_policy_keeps_veto_and_records_evidence() -> None:
    result = classify_decision("Company announces update", "<p>The firm reports earnings.</p>")
    assert result.source == "body"
    assert result.evidence == "The firm reports earnings."
    assert result.event_type == EventType.CORPORATE_EARNINGS
    assert classify_decision("Company announces update", "Bad � earnings.").allow_llm
    assert not classify_decision("Broken � title", "Good earnings.").allow_llm
    result = classify_decision("War Raiders clash", "WWE wrestling title bout")
    assert result.source == "body_veto"
    assert result.event_type == EventType.SPORT


def test_strict_modes_are_explicit_and_relevant_context_cannot_override_title() -> None:
    title, body = (
        "Acme announces update",
        "Elsewhere Beta reports earnings. Acme announces buyback.",
    )
    strict = classify_decision(title, body, mode="title_with_relevant_context")
    assert strict.event_type == EventType.SHARE_BUYBACK
    assert "Elsewhere" not in strict.evidence
    assert classify_decision(title, body, mode="title_only").event_type == EventType.OTHER
    assert classify_decision("Acme announces sanctions", body).event_type == EventType.SANCTIONS


@pytest.mark.parametrize(
    ("text", "kind", "polarity"),
    [
        (
            "New supply agreement signed with automaker",
            EventType.CONTRACT_WIN,
            EventPolarity.OCCURRENCE,
        ),
        (
            "Company cancels conference after earnings",
            EventType.CORPORATE_EARNINGS,
            EventPolarity.OCCURRENCE,
        ),
        ("Ceasefire expected after talks", EventType.MILITARY_CONFLICT, EventPolarity.OCCURRENCE),
        ("Ceasefire agreed after talks", EventType.MILITARY_CONFLICT, EventPolarity.RESOLUTION),
        (
            "Military calls off planned Iran attack",
            EventType.MILITARY_CONFLICT,
            EventPolarity.RESOLUTION,
        ),
    ],
)
def test_polarity_refers_to_selected_event(
    text: str, kind: EventType, polarity: EventPolarity
) -> None:
    assert classify_polarity(text, kind) == polarity


def test_modes_are_validated_and_versions_change_with_processing() -> None:
    base = CleansingSettings(classification_mode="title_first")
    for update in (
        {"classification_mode": "title_only"},
        {"nlp_backend": "spacy"},
        {"bge_model_name": "different-model"},
    ):
        assert base.model_copy(update=update).processing_version != base.processing_version
    with pytest.raises(ValidationError):
        CleansingSettings.model_validate({"classification_mode": "invalid"})
