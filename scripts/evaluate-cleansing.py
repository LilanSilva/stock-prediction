"""Replay the existing reviewed corpora locally; these are regression sets, not held-out data."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

from cleansing.extraction import SpacyExtractor
from cleansing.taxonomy import (
    NON_CLUSTERING_EVENT_TYPES,
    classify_decision,
    resolve_scope,
)
from shared.text import assess_text


def evaluate(
    articles: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    *,
    mode: str = "title_first",
    spacy: SpacyExtractor | None = None,
) -> dict[str, Any]:
    by_ref = {article["ref"]: article for article in articles}
    if len(by_ref) != len(articles) or len({label["ref"] for label in labels}) != len(
        labels
    ):
        raise ValueError("duplicate article/label references")
    if set(by_ref) != {label["ref"] for label in labels}:
        raise ValueError("articles and labels must cover the same references")
    rows = []
    for label in labels:
        article = by_ref[label["ref"]]
        body = article.get("body") or ""
        decision = classify_decision(
            article["title"],
            body,
            article.get("canonical_url", ""),
            mode=mode,
        )
        kind, keyword = decision.event_type, decision.keyword
        clean_title = assess_text(article["title"], title=True).usable
        assets = sorted(str(asset) for asset in resolve_scope(clean_title, kind).assets)
        if spacy is not None:
            action = asyncio.run(
                spacy.extract(
                    article["title"],
                    article.get("language", "en"),
                    body,
                    article.get("canonical_url", ""),
                )
            )
            kind = action.event_type
            assets = sorted(str(asset) for asset in action.affected_asset_ids)
        rows.append(
            {
                "ref": label["ref"],
                "expected": label["expected_event_type"],
                "actual": kind.value,
                "keyword": keyword,
                "assets": assets,
                "language": article.get("language", "unknown"),
                "body_quality": assess_text(body).status,
                "reason": decision.reason,
                "tier_type": decision.event_type.value,
                "verdict": label["verdict"],
                "expected_assets": label["expected_assets"],
            }
        )
    typed = [row for row in rows if row["expected"] is not None]
    per_class = {}
    for kind in sorted({r["expected"] for r in typed} | {r["actual"] for r in typed}):
        tp = sum(r["actual"] == r["expected"] == kind for r in typed)
        predicted = sum(r["actual"] == kind for r in typed)
        support = sum(r["expected"] == kind for r in typed)
        precision = tp / predicted if predicted else 0
        recall = tp / support if support else 0
        per_class[kind] = {
            "precision": precision,
            "recall": recall,
            "support": support,
            "f1": 2 * precision * recall / (precision + recall)
            if precision + recall
            else 0,
        }
    return {
        "count": len(rows),
        "typed_count": len(typed),
        "type_accuracy": sum(r["actual"] == r["expected"] for r in typed) / len(typed)
        if typed
        else None,
        "macro_f1": sum(v["f1"] for v in per_class.values()) / len(per_class)
        if per_class
        else None,
        "market_type_coverage": sum(
            r["actual"] not in NON_CLUSTERING_EVENT_TYPES for r in rows
        )
        / len(rows)
        if rows
        else 0,
        "by_language": {
            lang: {
                "count": len(group),
                "typed_count": len(scored),
                "type_accuracy": sum(r["actual"] == r["expected"] for r in scored)
                / len(scored)
                if scored
                else None,
            }
            for lang in sorted({r["language"] for r in rows})
            for group in [[r for r in rows if r["language"] == lang]]
            for scored in [[r for r in group if r["expected"] is not None]]
        },
        "body_quality_counts": dict(Counter(r["body_quality"] for r in rows)),
        "spacy_disagreement_refs": [
            r["ref"] for r in rows if r["actual"] != r["tier_type"]
        ],
        "per_class": per_class,
        "confusion": dict(Counter(f"{r['expected']} -> {r['actual']}" for r in typed)),
        "false_prediction_refs": [
            r["ref"] for r in rows if r["verdict"] == "must_not_predict" and r["assets"]
        ],
        "justified_asset_mismatches": [
            r["ref"]
            for r in rows
            if r["verdict"] == "justified"
            and r["assets"] != sorted(r["expected_assets"])
        ],
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "src/services/cleansing/tests/fixtures",
    )
    parser.add_argument(
        "--mode",
        choices=["title_first", "title_only", "title_with_relevant_context"],
        default="title_first",
    )
    parser.add_argument(
        "--spacy",
        action="store_true",
        help="Measure the installed spaCy models, no LLM calls",
    )
    args = parser.parse_args()
    spacy = SpacyExtractor(classification_mode=args.mode) if args.spacy else None
    if spacy is not None:
        spacy.load()
    reports = {}
    for path in sorted(args.fixtures.glob("audit-*-articles.json")):
        labels = path.with_name(path.name.replace("-articles", "-labels"))
        reports[path.stem] = evaluate(
            json.loads(path.read_text(encoding="utf-8"))["articles"],
            json.loads(labels.read_text(encoding="utf-8"))["labels"],
            mode=args.mode,
            spacy=spacy,
        )
    if not reports:
        parser.error("no audit-*-articles.json fixtures found")
    print(
        json.dumps(
            {
                "dataset_kind": "reviewed_regression_not_held_out",
                "mode": args.mode,
                "backend": "spacy" if spacy else "keyword",
                "reports": reports,
            }
        )
    )


if __name__ == "__main__":
    main()
