"""Tests for the audit metrics helper.

The helper is the measuring instrument for E12, so it is calibrated against a known reading: the
2026-08-12 export, whose figures are published in the epic README. If these tests drift, every
before/after comparison built on the helper is unsound.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from audit_metrics import _compare, _edge_labels, compute, is_propagated, load_export

_SCRIPTS = Path(__file__).parent
_EXPORT = _SCRIPTS / "prediction-audit-2026-08-12.json"
_VERDICTS = _SCRIPTS / "audit-verdicts-2026-08-12.json"

pytestmark = pytest.mark.skipif(
    not _EXPORT.exists(), reason="the 2026-08-12 export is not present"
)


@pytest.fixture(scope="module")
def export() -> dict:
    return load_export(_EXPORT)


@pytest.fixture(scope="module")
def verdicts() -> dict[str, str]:
    payload = json.loads(_VERDICTS.read_text(encoding="utf-8-sig"))
    return {v["prediction_id"]: v["verdict"] for v in payload["verdicts"]}


# --- calibration against the published figures ---------------------------------------------------


def test_reproduces_the_published_prediction_count(export: dict) -> None:
    assert compute(export)["prediction_count"] == 113


def test_reproduces_the_published_propagated_count(export: dict) -> None:
    # 39 of 113 predictions carried no causal factor: E12 defect 5.
    metrics = compute(export)
    assert metrics["propagated_count"] == 39
    assert metrics["propagated_share"] == pytest.approx(0.345, abs=0.001)


def test_reproduces_the_published_justified_share(export: dict, verdicts: dict[str, str]) -> None:
    metrics = compute(export, verdicts)
    assert metrics["verdict_breakdown"] == {"ok": 7, "medium": 7, "not_sure": 6, "wrong": 93}
    assert metrics["justified_count"] == 14
    assert metrics["justified_share"] == pytest.approx(0.124, abs=0.001)


def test_reproduces_the_finding_that_motivated_the_epic(
    export: dict, verdicts: dict[str, str]
) -> None:
    # Justified predictions hit 86%; unjustified ones hit 36%. This gap is the whole argument for E12,
    # so it is pinned rather than left to a report a reader has to trust.
    buckets = compute(export, verdicts)["hit_rate_by_verdict"]
    assert buckets["ok"]["hit_rate_decided"] == pytest.approx(0.857, abs=0.001)
    assert buckets["wrong"]["hit_rate_decided"] == pytest.approx(0.356, abs=0.001)
    assert buckets["ok"]["hit_rate_decided"] > buckets["wrong"]["hit_rate_decided"]


def test_detects_the_opposing_stances(export: dict) -> None:
    conflicted = compute(export)["assets_with_opposing_stances"]
    assert {"NEM_NYSE", "XOM_NYSE", "LUG_STO"} <= set(conflicted)


# --- edge-label parsing --------------------------------------------------------------------------


def test_edge_labels_read_through_mojibake_arrows() -> None:
    # Exports written before the encoding fix hold the arrows double-encoded. Splitting on the real
    # "↓" matched nothing there and silently reported 0% propagated.
    mojibake = "XOM_NYSE DOWN (SMALL, conf 1.00) from 1 causal edge(s): CORRELATIONΓåô0.30"
    assert _edge_labels(mojibake) == ["CORRELATION"]


def test_edge_labels_read_real_arrows() -> None:
    clean = "NEM_NYSE UP (MEDIUM, conf 1.00) from 1 causal edge(s): INFLATION_CHANGE↑0.45"
    assert _edge_labels(clean) == ["INFLATION_CHANGE"]


def test_mixed_edges_are_not_counted_as_propagated() -> None:
    # A decision combining a real factor with a propagated edge is still news-derived.
    mixed = {
        "rationale": "X UP (MEDIUM, conf 1.00) from 2 causal edge(s): "
        "MILITARY_CONFLICT↑0.50, CORRELATION↓0.30"
    }
    assert not is_propagated(mixed)


def test_prediction_without_edges_is_not_propagated() -> None:
    assert not is_propagated({"rationale": "no edge clause here"})


# --- the gate ------------------------------------------------------------------------------------


def _metrics(**overrides) -> dict:
    base = {
        "date": "2026-08-20",
        "prediction_count": 100,
        "propagated_share": 0.10,
        "assets_with_opposing_stances": [],
        "justified_share": 0.70,
    }
    base.update(overrides)
    return base


_BASELINE = _metrics(
    date="2026-08-12", prediction_count=113, propagated_share=0.345,
    assets_with_opposing_stances=["NEM_NYSE"], justified_share=0.124,
)


def test_gate_passes_when_precision_rises_and_volume_holds() -> None:
    assert _compare(_metrics(), _BASELINE) == 0


def test_gate_fails_on_a_volume_collapse() -> None:
    # The failure mode the gate exists for: a perfect justified share bought by predicting almost
    # nothing.
    assert _compare(_metrics(prediction_count=3, justified_share=1.0), _BASELINE) == 2


def test_gate_fails_below_the_target_even_when_improved() -> None:
    assert _compare(_metrics(justified_share=0.40), _BASELINE) == 2


def test_gate_fails_while_opposing_stances_remain() -> None:
    assert _compare(_metrics(assets_with_opposing_stances=["NEM_NYSE"]), _BASELINE) == 2


def test_gate_fails_when_propagated_share_grows() -> None:
    assert _compare(_metrics(propagated_share=0.50), _BASELINE) == 2
