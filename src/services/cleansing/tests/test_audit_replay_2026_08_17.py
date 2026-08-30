"""Replay of the 2026-08-17 cleansing audit through the real classifier.

An audit of every cluster produced that day (``scripts/cleansing-audit-2026-08-17.json``, with the
per-cluster verdicts in ``scripts/cleansing-misclassified-2026-08-17.md``) found 110 of 251 clusters
correctly classified. 222 were typed ``OTHER``, including roughly 85 sport, culture and lifestyle
articles that REF-01 §2.1 requires be *explicitly* rejected — the non-financial reject tier fired
twice all day, because its keyword vocabulary is English-first and every feed is Swedish.

This module is the executable form of that finding, and it is deliberately a different oracle from
``test_audit_replay.py``:

  * The 2026-08-12 corpus is labelled by **asset outcome** — 65 of its 73 labels are
    ``must_not_predict``. It cannot detect a classification regression, because an article typed
    ``OTHER`` instead of ``SPORT`` resolves to no assets either way and passes.
  * This corpus is labelled by **event type**, so it can.

Three assertions, deliberately separate:

  * **Type** — an article must classify to its labelled ``expected_event_type``. Refs listed in
    ``_KNOWN_MISCLASSIFIED`` do not yet, and that set is asserted to be *exactly* the failing set, so
    neither a fix nor a regression can pass unnoticed (see ``test_known_misclassified_set_is_exact``).
  * **Precision** — a ``must_not_predict`` article must resolve to no assets, so Prediction drops the
    event. Asserted for every article, including the not-yet-fixed ones: a wrong *type* is a defect,
    but a wrong type that also produces a prediction is the E12 defect class and must never regress.
  * **Recall** — a ``justified`` article must keep resolving to its labelled assets. Narrowing the
    classifier until nothing fires would satisfy precision alone.

Fixture provenance, the labelling rubric, and the rule that labels are corrected by review rather
than to make a test pass are in ``fixtures/README.md``.

Requirements verified end to end: CLN-69 – CLN-72 (see ``requirements/SRS-03-cleansing.md`` §5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from shared.schemas.messages import AssetId, EventType

from cleansing.taxonomy import classify_text, resolve_scope

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


_ARTICLES: dict[str, dict[str, Any]] = {
    a["ref"]: a for a in _load("audit-2026-08-17-articles.json")["articles"]
}
_LABELS: list[dict[str, Any]] = _load("audit-2026-08-17-labels.json")["labels"]
_IDS = [label["ref"] for label in _LABELS]

# Articles whose labelled type the classifier does not yet produce. This is a record of outstanding
# work, not a list of accepted behaviour: every entry is a known defect from the 2026-08-17 audit.
#
# It is asserted to be EXACTLY the set that currently fails. Removing a defect therefore breaks this
# test until the ref is deleted from the set, which forces the improvement to be recorded; introducing
# one breaks it too. Never add a ref to silence a failure without first checking the label — per
# fixtures/README.md the default assumption is that the classifier is wrong, not the label.
_KNOWN_MISCLASSIFIED = frozenset({
    "B1", "B3", "B5", "B6", "B7", "B11", "B12", "B14", "B15", "B16",
    "B27", "B28", "B37", "B39", "B42", "B43", "B45", "B47", "B48", "B52",
    "B59", "B64", "B68", "B73", "B74", "B115", "B116", "B120", "B122", "B134",
    "B137", "B141", "B144", "B145", "B147", "B157", "B158", "B160", "B162", "B176",
    "B179", "B185", "B190", "B191", "B192", "B195", "B199", "B202", "B203", "B204",
    "B213", "B220", "B221", "B222", "B223", "B227", "B229", "B230", "B231", "B235",
    "B240", "B244", "B246", "B247",
})

# Articles that are misclassified AND still reach an asset, so they produce a prediction today. This
# is the E12 defect class — a non-event moving a real instrument — and it is strictly worse than the
# type errors above, which mostly resolve to nothing and are merely wrong.
#
#   B145  "Så nära bor du en möjlig gruva" — a reader-service graphic about proximity to prospecting
#         permits. "tillstånd" in the body types it REGULATORY_ACTION, then the industry keyword
#         "gruva" fans out to Lundin Gold and the gold proxy.
#   B231  "Facktoppar bör avgå efter förlorad kamp mot Tesla" — a reader's opinion letter. "avgå"
#         types it POLITICAL_TRANSITION and the company keyword "tesla" attaches TSLA.
#   B235  "Vita huset: Trump skämtade om Hormuzsundet" — the White House stating the remark was a
#         joke. "hormuzsundet" types it STRAIT_CLOSURE and the ENERGY cue admits the oil names, so a
#         denial moves oil.
#
# Same ratchet as _KNOWN_MISCLASSIFIED: asserted to be exactly the leaking set, so fixing one forces
# its removal here and introducing one fails the build.
_KNOWN_ASSET_LEAKS = frozenset({"B145", "B231", "B235"})

# Share of labelled types the classifier gets right. Raised deliberately by each classification
# change; never lowered to make a test pass.
#   0.45  state measured at the 2026-08-17 audit, before any fix
#   0.53  CLN-70, macro vocabulary for quakes, wildfires, industrial production, retail, house
#         prices, EBITA/EBITDA, Ebola and measles, missile strikes, drones and launch ramps
#   0.72  CLN-71/CLN-72, the publisher-section tier — the single largest gain, and the only one that
#         does not depend on guessing vocabulary
_ACCURACY_FLOOR = 0.72


def _classify(ref: str) -> EventType:
    """Classify with every input the running service has, including the canonical URL.

    Omitting the URL here would silently exempt the publisher-section tier (CLN-71) from the whole
    corpus, so the oracle would pass while the deployed behaviour differed.
    """
    article = _ARTICLES[ref]
    event_type, _ = classify_text(article["title"], article["body"], article["canonical_url"])
    return event_type


def _assets(ref: str, event_type: EventType) -> tuple[AssetId, ...]:
    return resolve_scope(_ARTICLES[ref]["title"], event_type).assets


# --- corpus integrity ---------------------------------------------------------------------------


def test_corpus_and_labels_cover_the_same_articles() -> None:
    assert {label["ref"] for label in _LABELS} == set(_ARTICLES)


def test_every_article_carries_the_inputs_classification_needs() -> None:
    """A truncated body or a missing URL silently changes the answer, so the corpus must have both.

    The first export of this audit capped bodies at 500 characters, which made two clusters
    irreproducible: their type came from body text past the cut.
    """
    for ref, article in _ARTICLES.items():
        assert article["title"].strip(), ref
        assert article["canonical_url"].startswith("http"), ref
        assert "body" in article, ref


def test_known_misclassified_refs_exist_in_the_corpus() -> None:
    assert _KNOWN_MISCLASSIFIED <= set(_ARTICLES)


# --- type ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("label", _LABELS, ids=_IDS)
def test_event_type_matches_label(label: dict[str, Any]) -> None:
    ref = label["ref"]
    expected = label["expected_event_type"]
    if expected is None:
        # Downstream-equivalent: ENTERTAINMENT, LIFESTYLE and OTHER all resolve to zero assets and
        # are all excluded from Gate 2, so the exact type is not asserted. Precision still is.
        pytest.skip(f"{ref}: type is downstream-equivalent, asserted by precision instead")
    if ref in _KNOWN_MISCLASSIFIED:
        pytest.skip(f"{ref}: known outstanding defect from the 2026-08-17 audit")
    assert _classify(ref) is EventType(expected)


def test_known_misclassified_set_is_exact() -> None:
    """The outstanding-defect set must match reality exactly.

    This is what makes the fixture a ratchet. A fix removes an article from the failing set and
    breaks this test until the ref is deleted from ``_KNOWN_MISCLASSIFIED``; a regression adds one
    and breaks it too. Either way the change has to be acknowledged in the diff.
    """
    failing = {
        label["ref"]
        for label in _LABELS
        if label["expected_event_type"] is not None
        and _classify(label["ref"]) is not EventType(label["expected_event_type"])
    }
    newly_broken = sorted(failing - _KNOWN_MISCLASSIFIED)
    newly_fixed = sorted(_KNOWN_MISCLASSIFIED - failing)
    assert not newly_broken, (
        f"classification regressed for {newly_broken}: these matched their label before"
    )
    assert not newly_fixed, (
        f"classification improved for {newly_fixed} — remove them from _KNOWN_MISCLASSIFIED "
        f"and raise _ACCURACY_FLOOR"
    )


def test_accuracy_floor() -> None:
    asserted = [label for label in _LABELS if label["expected_event_type"] is not None]
    correct = sum(
        1
        for label in asserted
        if _classify(label["ref"]) is EventType(label["expected_event_type"])
    )
    accuracy = correct / len(asserted)
    assert accuracy >= _ACCURACY_FLOOR, (
        f"classification accuracy {accuracy:.3f} fell below the recorded floor {_ACCURACY_FLOOR}"
    )


# --- precision ----------------------------------------------------------------------------------


@pytest.mark.parametrize("label", _LABELS, ids=_IDS)
def test_must_not_predict_articles_resolve_to_no_assets(label: dict[str, Any]) -> None:
    """Asserted against the type the classifier ACTUALLY produces, not the labelled one.

    A misclassified article is a defect; a misclassified article that also reaches an asset is the
    E12 defect class — a sport report moving gold. This half must hold even while the type is still
    wrong, which is why it does not skip on ``_KNOWN_MISCLASSIFIED``.
    """
    if label["verdict"] != "must_not_predict":
        pytest.skip(f"{label['ref']}: labelled {label['verdict']}")
    ref = label["ref"]
    if ref in _KNOWN_ASSET_LEAKS:
        pytest.skip(f"{ref}: known outstanding asset leak from the 2026-08-17 audit")
    assert _assets(ref, _classify(ref)) == ()


def test_known_asset_leak_set_is_exact() -> None:
    """The set of articles that wrongly produce a prediction must match reality exactly.

    Kept separate from ``test_known_misclassified_set_is_exact`` because these two failures are not
    equally serious. A wrong type usually resolves to nothing and costs a missed cluster; a wrong type
    that reaches an asset produces a false prediction and a notification.
    """
    leaking = {
        label["ref"]
        for label in _LABELS
        if label["verdict"] == "must_not_predict"
        and _assets(label["ref"], _classify(label["ref"])) != ()
    }
    newly_leaking = sorted(leaking - _KNOWN_ASSET_LEAKS)
    newly_sealed = sorted(_KNOWN_ASSET_LEAKS - leaking)
    assert not newly_leaking, (
        f"these articles now produce a prediction and must not: {newly_leaking}"
    )
    assert not newly_sealed, (
        f"asset leak fixed for {newly_sealed} — remove them from _KNOWN_ASSET_LEAKS"
    )


# --- recall -------------------------------------------------------------------------------------


@pytest.mark.parametrize("label", _LABELS, ids=_IDS)
def test_justified_articles_resolve_to_their_labelled_assets(label: dict[str, Any]) -> None:
    if label["verdict"] != "justified":
        pytest.skip(f"{label['ref']}: labelled {label['verdict']}")
    ref = label["ref"]
    if ref in _KNOWN_MISCLASSIFIED:
        pytest.skip(f"{ref}: known outstanding defect; recall is asserted once the type is fixed")
    expected = {AssetId(asset) for asset in label["expected_assets"]}
    assert set(_assets(ref, _classify(ref))) == expected
