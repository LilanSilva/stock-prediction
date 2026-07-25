"""Outcome join, paired metrics, and final P06 decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evaluation import load_jsonl
from market_policy import POLICY, resolve_sessions


CLASSES = ("UP", "DOWN", "NEUTRAL")


def actual_outcome(baseline: float, settlement: float) -> dict[str, Any]:
    change = (settlement - baseline) / baseline
    direction = "NEUTRAL" if abs(change) < 0.003 else "UP" if change > 0 else "DOWN"
    absolute = abs(change)
    magnitude = "SMALL" if absolute < 0.01 else "MEDIUM" if absolute < 0.03 else "LARGE"
    return {"return": change, "direction": direction, "magnitude": magnitude}


def balanced_accuracy(actual: list[str], predicted: list[str]) -> float | None:
    recalls = []
    for label in CLASSES:
        indexes = [index for index, value in enumerate(actual) if value == label]
        if indexes:
            recalls.append(sum(predicted[index] == label for index in indexes) / len(indexes))
    return sum(recalls) / len(recalls) if recalls else None


def macro_f1(actual: list[str], predicted: list[str]) -> float | None:
    scores = []
    for label in CLASSES:
        true_positive = sum(a == label and p == label for a, p in zip(actual, predicted))
        false_positive = sum(a != label and p == label for a, p in zip(actual, predicted))
        false_negative = sum(a == label and p != label for a, p in zip(actual, predicted))
        denominator = 2 * true_positive + false_positive + false_negative
        if denominator:
            scores.append(2 * true_positive / denominator)
    return sum(scores) / len(scores) if scores else None


def brier(actual: str, predicted: str, confidence: float) -> float:
    remaining = (1 - confidence) / 2
    probabilities = {label: remaining for label in CLASSES}
    probabilities[predicted] = confidence
    return sum((probabilities[label] - (1.0 if label == actual else 0.0)) ** 2 for label in CLASSES)


def method_metrics(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    eligible = [row for row in rows if method in row["predictions"]]
    actual = [row["actual"]["direction"] for row in eligible]
    predicted = [row["predictions"][method]["direction"] for row in eligible]
    correct = [left == right for left, right in zip(actual, predicted)]
    briers = [
        brier(
            row["actual"]["direction"],
            row["predictions"][method]["direction"],
            float(row["predictions"][method].get("confidence", 1 / 3)),
        )
        for row in eligible
    ]
    return {
        "contexts": len(eligible),
        "correct": sum(correct),
        "accuracy": sum(correct) / len(correct) if correct else None,
        "balanced_accuracy": balanced_accuracy(actual, predicted),
        "macro_f1": macro_f1(actual, predicted),
        "mean_brier": sum(briers) / len(briers) if briers else None,
        "class_support": {label: actual.count(label) for label in CLASSES},
    }


def compute_report(
    contexts: list[dict[str, Any]],
    baselines: dict[str, Any],
    bedrock: dict[str, Any],
    market: dict[str, Any],
) -> dict[str, Any]:
    graph = {item["context_id"]: item for item in baselines["graph_only"]}
    neutral = {item["context_id"]: item for item in baselines["always_neutral"]}
    majority = {item["context_id"]: item for item in baselines["predeclared_majority"]}
    model: dict[tuple[str, str], dict[str, Any]] = {}
    errors = []
    for item in bedrock["results"]:
        if item.get("status") != "OK":
            errors.append(item)
            continue
        model[(item["context_id"], item["mode"])] = item["prediction"]

    rows: list[dict[str, Any]] = []
    for context in contexts:
        asset_id = context["asset_id"]
        observations = market[asset_id]["observations"]
        decision_at = datetime.fromisoformat(context["window_start"]) + timedelta(minutes=60)
        timezone_name = POLICY["assets"][asset_id]["timezone"]
        baseline_close, settlement_close = resolve_sessions(observations, decision_at, timezone_name)
        actual = actual_outcome(baseline_close["close"], settlement_close["close"])
        observations_hash = hashlib.sha256(
            json.dumps([baseline_close, settlement_close], sort_keys=True).encode("utf-8")
        ).hexdigest()
        predictions = {
            "graph_only": graph[context["context_id"]],
            "always_neutral": neutral[context["context_id"]],
            "predeclared_majority": majority[context["context_id"]],
        }
        kg = model.get((context["context_id"], "KG_LLM"))
        llm = model.get((context["context_id"], "LLM_ONLY"))
        if kg:
            predictions["kg_llm"] = kg
        if llm:
            predictions["llm_only"] = llm
        rows.append(
            {
                "context_id": context["context_id"],
                "context_hash": context["context_hash"],
                "asset_id": asset_id,
                "decision_at": decision_at.isoformat(),
                "baseline": baseline_close,
                "settlement": settlement_close,
                "observations_hash": observations_hash,
                "actual": actual,
                "predictions": predictions,
            }
        )

    graph_correct = {
        row["context_id"]: row["predictions"]["graph_only"]["direction"] == row["actual"]["direction"]
        for row in rows
    }
    kg_correct = {
        row["context_id"]: row["predictions"].get("kg_llm", {}).get("direction") == row["actual"]["direction"]
        for row in rows
        if "kg_llm" in row["predictions"]
    }
    corrected = [key for key in kg_correct if kg_correct[key] and not graph_correct[key]]
    harmed = [key for key in kg_correct if not kg_correct[key] and graph_correct[key]]

    methods = {
        method: method_metrics(rows, method)
        for method in ("graph_only", "kg_llm", "llm_only", "always_neutral", "predeclared_majority")
    }
    complete = not errors and methods["kg_llm"]["contexts"] == 30 and methods["llm_only"]["contexts"] == 10
    if not complete:
        decision = "REVISE"
        reason = "The controlled model run was incomplete or contained invalid responses."
    elif len(corrected) > len(harmed):
        decision = "PASS"
        reason = "KG-plus-LLM corrected more graph-only decisions than it harmed."
    else:
        decision = "STOP"
        reason = "KG-plus-LLM did not improve enough over graph-only to justify implementation."

    processed: set[str] = set()
    alpha_beta: dict[str, list[float]] = defaultdict(lambda: [1.0, 1.0])
    changes = 0
    duplicate_skips = 0
    for _ in range(2):
        for row in rows:
            identity = row["context_id"]
            if identity in processed:
                duplicate_skips += 1
                continue
            processed.add(identity)
            changes += 1
            correct = graph_correct[identity]
            bucket = alpha_beta[row["asset_id"]]
            bucket[0 if correct else 1] += 1

    usage = bedrock.get("budget", {})
    actual_cost = round(
        usage.get("provider_input_tokens", 0) / 1_000_000 * 3
        + usage.get("provider_output_tokens", 0) / 1_000_000 * 15,
        6,
    )
    return {
        "decision": decision,
        "reason": reason,
        "small_sample_limitation": "Thirty conflict contexts are exploratory evidence, not production-grade accuracy proof.",
        "contexts": len(rows),
        "methods": methods,
        "paired": {"corrected": corrected, "harmed": harmed, "net": len(corrected) - len(harmed)},
        "bedrock": {
            "model_id": bedrock.get("model_id"),
            "calls": usage.get("calls", 0),
            "input_tokens": usage.get("provider_input_tokens", 0),
            "output_tokens": usage.get("provider_output_tokens", 0),
            "actual_list_price_usd": actual_cost,
            "errors": errors,
        },
        "duplicate_learning_replay": {
            "first_pass_changes": changes,
            "second_pass_duplicate_skips": duplicate_skips,
            "state": dict(alpha_beta),
        },
        "rows": rows,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute final P06 paired metrics")
    parser.add_argument("--contexts", type=Path, default=Path("data/frozen/conflict-contexts.jsonl"))
    parser.add_argument("--baselines", type=Path, default=Path("results/baselines.json"))
    parser.add_argument("--bedrock", type=Path, default=Path("results/bedrock-evaluation.json"))
    parser.add_argument("--market", type=Path, default=Path("data/frozen/market-observations.json"))
    parser.add_argument("--output", type=Path, default=Path("results/final-decision.json"))
    args = parser.parse_args(argv)
    report = compute_report(
        load_jsonl(args.contexts),
        json.loads(args.baselines.read_text(encoding="utf-8")),
        json.loads(args.bedrock.read_text(encoding="utf-8")),
        json.loads(args.market.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("decision", "reason", "contexts", "paired", "bedrock")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
