"""Replay of the 2026-08-12 prediction audit through the real classifier.

The audit found 93 of 113 predictions were not justified by their contributing news
(backlog/E12-Prediction-Quality-Remediation). This module is the executable form of that finding: it
runs all 73 distinct articles through ``classify_text`` and ``resolve_scope`` and asserts the
labelled outcome.

Two assertions, deliberately separate:

  * **Precision** — an article labelled ``must_not_predict`` must resolve to no assets, so
    Prediction drops the event. This is the class of defect the audit found.
  * **Recall** — an article labelled ``justified`` must keep resolving to the assets that made it a
    good prediction. Narrowing the classifier until nothing fires would satisfy precision alone, so
    this half is what makes the suite meaningful.

Fixture provenance, the labelling rubric, and the rule that labels are corrected by review rather
than to make a test pass are in ``fixtures/README.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from shared.schemas.messages import AssetId, EventType

from cleansing.taxonomy import NON_FINANCIAL_EVENT_TYPES, classify_text, resolve_scope

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


_ARTICLES: dict[str, dict[str, Any]] = {
    a["ref"]: a for a in _load("audit-2026-08-12-articles.json")["articles"]
}
_LABELS: list[dict[str, Any]] = _load("audit-2026-08-12-labels.json")["labels"]

# Verdicts that assert an exact asset set. `known_recall_gap` is documented but not asserted: it
# records a prediction that *should* be produced and is not, for a reason outside E12's scope.
_ASSERTED = ("must_not_predict", "justified", "accepted_limitation")

_IDS = [label["ref"] for label in _LABELS]


def _classify(ref: str) -> tuple[EventType, tuple[AssetId, ...]]:
    article = _ARTICLES[ref]
    event_type, _ = classify_text(article["title"], article["body"])
    return event_type, resolve_scope(article["title"], event_type).assets


# --- corpus integrity ---------------------------------------------------------------------------


def test_corpus_and_labels_cover_the_same_articles() -> None:
    assert {label["ref"] for label in _LABELS} == set(_ARTICLES)
    assert len(_ARTICLES) == 73


def test_corpus_encoding_was_repaired() -> None:
    # The source export is double-encoded (cp437 over utf-8), so a faithful corpus contains real
    # Swedish characters and none of the mojibake sequences the export is full of.
    titles = " ".join(a["title"] for a in _ARTICLES.values())
    assert "läge" in titles
    assert "├" not in titles
    assert "ΓÇ" not in titles


# --- the replay ---------------------------------------------------------------------------------


@pytest.mark.parametrize("label", _LABELS, ids=_IDS)
def test_audit_article_resolves_to_labelled_assets(label: dict[str, Any]) -> None:
    if label["verdict"] not in _ASSERTED:
        pytest.skip(f"{label['ref']}: {label['verdict']} — documented, not asserted")

    _, assets = _classify(label["ref"])
    expected = {AssetId(a) for a in label["expected_assets"]}
    assert set(assets) == expected, (
        f"{label['ref']} {label['title']!r}\n"
        f"  verdict:   {label['verdict']}\n"
        f"  rationale: {label['rationale']}"
    )


@pytest.mark.parametrize(
    "label", [x for x in _LABELS if x["expected_event_type"]], ids=lambda x: x["ref"]
)
def test_audit_article_classifies_to_labelled_event_type(label: dict[str, Any]) -> None:
    event_type, _ = _classify(label["ref"])
    assert event_type is EventType(label["expected_event_type"]), (
        f"{label['ref']} {label['title']!r}\n  rationale: {label['rationale']}"
    )


# --- aggregate properties -----------------------------------------------------------------------


def test_no_non_financial_article_resolves_to_an_asset() -> None:
    """A SPORT/ENTERTAINMENT/LIFESTYLE article carries no causal event, so it moves nothing.

    Asserted over the whole corpus rather than per label, so a future article that starts
    classifying as non-financial is covered without editing the labels.
    """
    for ref in _ARTICLES:
        event_type, assets = _classify(ref)
        if event_type in NON_FINANCIAL_EVENT_TYPES:
            assert assets == (), f"{ref} typed {event_type.value} but resolved {assets}"


def test_justified_articles_still_predict() -> None:
    """The recall half. Precision bought by breaking these is not an improvement."""
    justified = [x for x in _LABELS if x["verdict"] == "justified"]
    assert justified, "corpus lost its justified cases"
    for label in justified:
        _, assets = _classify(label["ref"])
        assert assets, f"{label['ref']} produced no assets: {label['rationale']}"


def test_gold_forecast_does_not_reach_the_oil_proxy() -> None:
    """A22 is the case that pins the per-family cue gate.

    A gold price forecast must move the gold proxies and must not move the oil proxy, even though
    both sit behind the same event-type fallback.
    """
    _, assets = _classify("A22")
    assert AssetId.NEM_NYSE in assets
    assert AssetId.XOM_NYSE not in assets
    assert AssetId.IPCO_STO not in assets
