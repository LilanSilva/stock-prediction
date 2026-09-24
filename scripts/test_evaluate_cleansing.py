"""Evaluator arithmetic, independent of the asset registry's contents."""

from pathlib import Path
from runpy import run_path

import pytest

evaluate = run_path(str(Path(__file__).with_name("evaluate-cleansing.py")))["evaluate"]


def test_unknown_labels_do_not_count_towards_type_accuracy() -> None:
    articles = [
        {"ref": "a", "title": "A quiet day", "language": "en"},
        {"ref": "b", "title": "A quiet day", "language": "sv"},
    ]
    labels = [
        {
            "ref": "a",
            "expected_event_type": "OTHER",
            "expected_assets": [],
            "verdict": "must_not_predict",
        },
        {
            "ref": "b",
            "expected_event_type": None,
            "expected_assets": [],
            "verdict": "must_not_predict",
        },
    ]
    report = evaluate(articles, labels)
    assert report["count"] == 2 and report["typed_count"] == 1
    assert report["type_accuracy"] == 1
    assert report["by_language"]["sv"]["type_accuracy"] is None
    assert report["false_prediction_refs"] == []
    assert evaluate([], [])["type_accuracy"] is None
    with pytest.raises(ValueError, match="same references"):
        evaluate(articles, labels[:1])
