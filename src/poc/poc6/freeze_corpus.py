"""Build high-precision, outcome-blind P06 conflict candidates and freeze reviewed IDs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collector import load_corpus
from poc6 import Article, EventSignal, SignalRule, context_window_start, normalize_title


STRICT_RULES = (
    SignalRule(
        "military_conflict",
        "MILITARY_CONFLICT",
        (
            r"\b(?:iran|ukraine|russia|israel|gaza|military|missile|drone|air)\b.{0,45}\b(?:war|attack|strike|bomb)",
            r"\bwar\b.{0,45}\b(?:iran|ukraine|russia|israel|gaza|military|missile|drone|attack|strike)",
            r"\b(?:iran|ukraine|russia|israel|gaza)\s+war\b",
        ),
        {"GOLD": 1, "BRENT_OIL": 1},
    ),
    SignalRule(
        "strait_closure",
        "STRAIT_CLOSURE",
        (
            r"\b(?:hormuz|strait)\b.{0,45}\b(?:clos|block|restrict|reopen)",
            r"\b(?:clos|block|restrict|reopen)\w*\b.{0,45}\b(?:hormuz|strait)\b",
        ),
        {"GOLD": -1, "BRENT_OIL": 1},
    ),
    SignalRule(
        "supply_disruption",
        "SUPPLY_DISRUPTION",
        (
            r"\b(?:oil|gas|pipeline|production|supply)\b.{0,45}\b(?:halt|cut|disrupt|damage|stop|shortage)",
            r"\b(?:halt|cut|disrupt|damage|stop)\w*\b.{0,45}\b(?:oil|gas|pipeline|production|supply)\b",
        ),
        {"BRENT_OIL": 1},
    ),
    SignalRule(
        "sanctions",
        "SANCTIONS",
        (r"\b(?:impose|expand|tighten|new|lift|ease)\w*\s+sanctions?\b",),
        {"GOLD": 1, "BRENT_OIL": 1},
    ),
    SignalRule(
        "rate_hike",
        "RATE_DECISION",
        (
            r"\brate(?:s)?\s+(?:hike|hikes|raised|increase|increases)\b",
            r"\b(?:hike|raise|raises|raised|increase)\w*\s+(?:interest\s+)?rates?\b",
        ),
        {"GOLD": -1},
    ),
    SignalRule(
        "rate_cut",
        "RATE_DECISION",
        (
            r"\brate(?:s)?\s+(?:cut|cuts|lowered|decrease|decreases)\b",
            r"\b(?:cut|lower|lowers|lowered|decrease)\w*\s+(?:interest\s+)?rates?\b",
        ),
        {"GOLD": 1},
    ),
    SignalRule(
        "inflation_rise",
        "INFLATION_CHANGE",
        (r"\binflation\b.{0,35}\b(?:rise|rises|rose|higher|accelerat|increase)\w*\b",),
        {"GOLD": 1},
    ),
    SignalRule(
        "inflation_fall",
        "INFLATION_CHANGE",
        (r"\binflation\b.{0,35}\b(?:fall|falls|fell|lower|slow|cool|decrease)\w*\b",),
        {"GOLD": -1},
    ),
    SignalRule(
        "recession_signal",
        "RECESSION_SIGNAL",
        (r"\b(?:economy|economic|gdp|uk|us|eurozone|global|country|market)\b.{0,50}\brecession\b", r"\brecession\b.{0,50}\b(?:economy|economic|gdp|risk|forecast|job|market)\b"),
        {"GOLD": 1, "BRENT_OIL": -1},
    ),
)


def detect_strict(article: Article) -> list[EventSignal]:
    title = normalize_title(article.title)
    signals: list[EventSignal] = []
    for rule in STRICT_RULES:
        if rule.name in {"rate_hike", "rate_cut"}:
            monetary_context = re.search(
                r"\b(?:fed|federal reserve|central bank|bank of|boj|ecb|bsp|interest|monetary|policy rate|economist|investor|borrower|housing|mortgage|yen|peso)\b",
                title,
                flags=re.IGNORECASE,
            )
            non_monetary = re.search(
                r"\b(?:tax|utility|utilities|water|sewer|trash|dte|duke energy|wilkesboro)\b",
                title,
                flags=re.IGNORECASE,
            )
            if not monetary_context or non_monetary:
                continue
        if not any(re.search(pattern, title, flags=re.IGNORECASE) for pattern in rule.patterns):
            continue
        for asset_id, sign in rule.forces.items():
            identity = f"{article.url}|{rule.name}|{asset_id}"
            signals.append(
                EventSignal(
                    event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                    source_id=article.source_id,
                    title=article.title,
                    url=article.url,
                    published_at=article.published_at,
                    rule=rule.name,
                    event_type=rule.event_type,
                    asset_id=asset_id,
                    direction="UP" if sign > 0 else "DOWN",
                )
            )
    return signals


def build_candidates(records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[EventSignal]] = {}
    for record in records.values():
        article = Article(
            record["source_id"],
            record["title"],
            record["canonical_url"],
            record["published_at_utc"],
            record.get("published_at_original"),
        )
        for signal in detect_strict(article):
            grouped.setdefault(
                (signal.asset_id, context_window_start(signal.published_at)), []
            ).append(signal)

    candidates: list[dict[str, Any]] = []
    for (asset_id, window_start), signals in sorted(grouped.items()):
        selected: dict[tuple[str, str], EventSignal] = {}
        for signal in signals:
            key = (signal.direction, signal.rule)
            existing = selected.get(key)
            if existing is None or normalize_title(signal.title) < normalize_title(existing.title):
                selected[key] = signal
        retained = sorted(selected.values(), key=lambda item: (item.direction, item.rule, item.event_id))
        if {item.direction for item in retained} != {"UP", "DOWN"}:
            continue
        if len({item.url for item in retained}) < 2:
            continue
        context_id = hashlib.sha256(f"{asset_id}|{window_start}".encode("utf-8")).hexdigest()[:24]
        evidence = [asdict(item) for item in retained]
        context_hash = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        candidates.append(
            {
                "context_id": context_id,
                "context_hash": context_hash,
                "context_version": 1,
                "asset_id": asset_id,
                "window_start": window_start,
                "window_minutes": 60,
                "graph_fixture_version": "p06-graph-v1",
                "signals": evidence,
                "outcome_joined": False,
            }
        )
    return candidates


def freeze_reviewed(
    candidates: list[dict[str, Any]], review_path: Path, output: Path
) -> dict[str, Any]:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    accepted = set(review["accepted_context_ids"])
    frozen = [item for item in candidates if item["context_id"] in accepted]
    missing = accepted - {item["context_id"] for item in frozen}
    if missing:
        raise ValueError(f"Review references unknown context IDs: {sorted(missing)}")
    if len(frozen) < 30:
        raise ValueError(f"At least 30 reviewed contexts are required; found {len(frozen)}")
    for item in frozen:
        item["review"] = {
            "reviewed_by": review["reviewed_by"],
            "reviewed_at": review["reviewed_at"],
            "outcomes_visible": False,
            "decision": "ACCEPT",
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for item in frozen:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(output)
    return {
        "status": "FROZEN",
        "contexts": len(frozen),
        "assets": sorted({item["asset_id"] for item in frozen}),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "outcomes_joined": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or freeze P06 conflict contexts")
    parser.add_argument("--corpus", type=Path, default=Path("data/raw/articles.jsonl"))
    parser.add_argument("--candidates", type=Path, default=Path("data/review/candidates.json"))
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/frozen/conflict-contexts.jsonl"))
    args = parser.parse_args(argv)

    candidates = build_candidates(load_corpus(args.corpus))
    args.candidates.parent.mkdir(parents=True, exist_ok=True)
    args.candidates.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report: dict[str, Any] = {"status": "REVIEW_REQUIRED", "candidates": len(candidates)}
    if args.review:
        report = freeze_reviewed(candidates, args.review, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
