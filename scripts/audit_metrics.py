#!/usr/bin/env python
"""Metrics for a prediction-audit export, so a quality change is measured rather than asserted.

Why this exists separately from ``measure-prediction-accuracy.py``: that script reads the database and
answers "were the predictions right?". This one reads an audit export and answers "were the
predictions *justified by their news*?" — the question the 2026-08-12 audit raised, where 93 of 113
predictions were logically unsupported yet 36% of them happened to be directionally correct.

The two numbers that must be read together
------------------------------------------
**Justified share** rises trivially if the pipeline stops predicting. **Volume** falls silently when a
classifier is over-narrowed. Reporting either alone hides the other's failure mode, so this script
always prints both and the E12 gate requires both.

Justified share needs human verdicts, which live in a companion file
(``audit-verdicts-<date>.json``) because no code can derive them. Without one, the mechanical metrics
are still reported.

Usage:
    python scripts/audit_metrics.py scripts/prediction-audit-2026-08-12.json
    python scripts/audit_metrics.py <export.json> --verdicts scripts/audit-verdicts-2026-08-12.json
    python scripts/audit_metrics.py <export.json> --baseline-out scripts/audit-baseline.json
    python scripts/audit_metrics.py <new.json> --compare scripts/audit-baseline-2026-08-12.json

Exit code is 0 when the report is produced, 1 when the export cannot be read, and 2 when a
``--compare`` gate fails.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Any

# A prediction whose every contributing edge is a propagated CORRELATES_WITH edge carries no causal
# factor: `decide()` labels those "CORRELATION" because `factor_id` is None (see prediction/decision.py
# `_factor_label`). They are derivative stances, not news-derived ones, and on 2026-08-12 they were 35%
# of all output at a 32% hit rate against 47% for direct predictions.
_PROPAGATED_LABEL = "CORRELATION"

# Verdicts that count as "the prediction followed from its news". `medium` is included because the
# topic and direction were right and only the mechanism label was overstated.
_JUSTIFIED_VERDICTS = frozenset({"ok", "medium"})


def load_export(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _edge_labels(rationale: str) -> list[str]:
    """The factor labels from a rationale's trailing ``... causal edge(s): A↑0.50, B↓0.30`` clause.

    The label is read as the leading upper-case run rather than by splitting on the arrow, because
    exports written before the encoding fix hold the arrows as mojibake (``Γåô`` for ``↓``) and
    splitting on the real character silently matched nothing.
    """
    _, _, tail = rationale.partition("edge(s):")
    if not tail:
        return []
    return [
        match.group(0)
        for match in (re.match(r"[A-Z_]+", part.strip()) for part in tail.split(","))
        if match
    ]


def is_propagated(prediction: dict[str, Any]) -> bool:
    labels = _edge_labels(prediction.get("rationale", ""))
    return bool(labels) and all(label == _PROPAGATED_LABEL for label in labels)


def compute(export: dict[str, Any], verdicts: dict[str, str] | None = None) -> dict[str, Any]:
    """Reduce an export to the metrics the E12 gate is judged on."""
    predictions = export["predictions"]
    total = len(predictions)
    propagated = [p for p in predictions if is_propagated(p)]
    by_asset = collections.Counter(p["asset_id"] for p in predictions)
    verification = collections.Counter(p.get("verification", "unknown") for p in predictions)

    # Opposing stances on one asset. On 2026-08-12 this was structural, not occasional: one event
    # reached both ends of an anti-correlated pair and each end propagated a contradiction back.
    directions_by_asset: dict[str, set[str]] = collections.defaultdict(set)
    for p in predictions:
        directions_by_asset[p["asset_id"]].add(p["predicted_dir"])
    conflicted = sorted(a for a, dirs in directions_by_asset.items() if len(dirs) > 1)

    articles = {
        n["article_id"]
        for p in predictions
        for n in p.get("contributing_news", [])
    }

    metrics: dict[str, Any] = {
        "date": export.get("date"),
        "prediction_count": total,
        "distinct_assets": len(by_asset),
        "distinct_articles": len(articles),
        "predictions_per_asset_max": max(by_asset.values(), default=0),
        "top_assets": by_asset.most_common(5),
        "propagated_count": len(propagated),
        "propagated_share": round(len(propagated) / total, 4) if total else 0.0,
        "assets_with_opposing_stances": conflicted,
        "verification": dict(verification),
        "confidence_at_one": sum(1 for p in predictions if str(p.get("confidence")) == "1.000"),
    }

    decided = verification["correct"] + verification["wrong"]
    metrics["hit_rate_decided"] = (
        round(verification["correct"] / decided, 4) if decided else None
    )

    if verdicts:
        labelled = [p for p in predictions if p["prediction_id"] in verdicts]
        justified = [p for p in labelled if verdicts[p["prediction_id"]] in _JUSTIFIED_VERDICTS]
        metrics["verdicts_available"] = len(labelled)
        metrics["justified_count"] = len(justified)
        metrics["justified_share"] = (
            round(len(justified) / len(labelled), 4) if labelled else None
        )
        metrics["verdict_breakdown"] = dict(
            collections.Counter(verdicts[p["prediction_id"]] for p in labelled)
        )
        # The finding that motivated E12: unjustified predictions are near chance, justified ones
        # are not. Reported per bucket so a change cannot hide behind the blended number.
        per_bucket: dict[str, dict[str, Any]] = {}
        for verdict in sorted(set(verdicts.values())):
            bucket = [p for p in labelled if verdicts[p["prediction_id"]] == verdict]
            correct = sum(1 for p in bucket if p.get("verification") == "correct")
            wrong = sum(1 for p in bucket if p.get("verification") == "wrong")
            per_bucket[verdict] = {
                "count": len(bucket),
                "correct": correct,
                "wrong": wrong,
                "hit_rate_decided": round(correct / (correct + wrong), 4)
                if (correct + wrong)
                else None,
            }
        metrics["hit_rate_by_verdict"] = per_bucket

    return metrics


def _print_report(metrics: dict[str, Any]) -> None:
    print(f"Audit metrics for {metrics['date']}")
    print("=" * 60)
    print(f"  Predictions              {metrics['prediction_count']}")
    print(f"  Distinct assets          {metrics['distinct_assets']}")
    print(f"  Distinct articles        {metrics['distinct_articles']}")
    print(f"  Max per asset            {metrics['predictions_per_asset_max']}")
    print(f"  Propagated (no factor)   {metrics['propagated_count']} "
          f"({metrics['propagated_share']:.1%})")
    print(f"  confidence == 1.000      {metrics['confidence_at_one']}")
    print(f"  Verification             {metrics['verification']}")
    if metrics["hit_rate_decided"] is not None:
        print(f"  Hit rate (decided)       {metrics['hit_rate_decided']:.1%}")
    if metrics["assets_with_opposing_stances"]:
        print(f"  OPPOSING STANCES on      {', '.join(metrics['assets_with_opposing_stances'])}")
    if "justified_share" in metrics and metrics["justified_share"] is not None:
        print()
        print(f"  Justified share          {metrics['justified_share']:.1%} "
              f"({metrics['justified_count']}/{metrics['verdicts_available']})")
        print(f"  Verdict breakdown        {metrics['verdict_breakdown']}")
        print("  Hit rate by verdict:")
        for verdict, stats in metrics["hit_rate_by_verdict"].items():
            rate = stats["hit_rate_decided"]
            shown = f"{rate:.1%}" if rate is not None else "n/a"
            print(f"    {verdict:<10} n={stats['count']:<4} hit={shown}")
    print()
    print("  Top assets:")
    for asset, count in metrics["top_assets"]:
        print(f"    {asset:<16} {count}")


def _compare(current: dict[str, Any], baseline: dict[str, Any]) -> int:
    """Gate a new export against a committed baseline. Returns a process exit code.

    Both directions fail: a precision gain bought with a volume collapse is not a gain.
    """
    print()
    print("Comparison against baseline")
    print("=" * 60)
    failures: list[str] = []

    base_volume = baseline["prediction_count"]
    volume = current["prediction_count"]
    floor = base_volume * 0.25
    print(f"  Volume         {base_volume} -> {volume}  (floor {floor:.0f})")
    if volume < floor:
        failures.append(
            f"prediction volume collapsed: {volume} < 25% of baseline {base_volume}"
        )

    base_just = baseline.get("justified_share")
    just = current.get("justified_share")
    if base_just is not None and just is not None:
        print(f"  Justified      {base_just:.1%} -> {just:.1%}  (target 60%)")
        if just < 0.60:
            failures.append(f"justified share {just:.1%} is below the 60% E12 target")
        if just < base_just:
            failures.append(f"justified share regressed: {base_just:.1%} -> {just:.1%}")
    else:
        print("  Justified      not comparable (no verdicts for one side)")

    base_prop = baseline["propagated_share"]
    prop = current["propagated_share"]
    print(f"  Propagated     {base_prop:.1%} -> {prop:.1%}")
    if prop > base_prop:
        failures.append(f"propagated share grew: {base_prop:.1%} -> {prop:.1%}")

    conflicted = current["assets_with_opposing_stances"]
    print(f"  Opposing       {len(baseline['assets_with_opposing_stances'])} -> {len(conflicted)}")
    if conflicted:
        failures.append(f"assets still hold opposing stances: {', '.join(conflicted)}")

    print()
    if failures:
        print("GATE FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 2
    print("GATE PASSED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("export", type=Path, help="prediction-audit-<date>.json")
    parser.add_argument("--verdicts", type=Path, help="audit-verdicts-<date>.json")
    parser.add_argument("--baseline-out", type=Path, help="write the computed metrics as a baseline")
    parser.add_argument("--compare", type=Path, help="gate against a committed baseline")
    args = parser.parse_args(argv)

    try:
        export = load_export(args.export)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {args.export}: {exc}", file=sys.stderr)
        return 1

    verdicts: dict[str, str] | None = None
    if args.verdicts:
        try:
            payload = json.loads(args.verdicts.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read {args.verdicts}: {exc}", file=sys.stderr)
            return 1
        verdicts = {v["prediction_id"]: v["verdict"] for v in payload["verdicts"]}

    metrics = compute(export, verdicts)
    _print_report(metrics)

    if args.baseline_out:
        args.baseline_out.write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nBaseline written to {args.baseline_out}")

    if args.compare:
        try:
            baseline = json.loads(args.compare.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read {args.compare}: {exc}", file=sys.stderr)
            return 1
        return _compare(metrics, baseline)

    return 0


if __name__ == "__main__":
    sys.exit(main())
