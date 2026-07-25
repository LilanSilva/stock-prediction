"""One-command completion of the already-frozen P06 experiment."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from evaluation import load_jsonl, run_bedrock, write_stable
from market_policy import PRICE_SERIES, fetch_observations
from metrics import compute_report


ROOT = Path(__file__).parent


def main() -> int:
    contexts_path = ROOT / "data/frozen/conflict-contexts.jsonl"
    baselines_path = ROOT / "results/baselines.json"
    bedrock_path = ROOT / "results/bedrock-evaluation.json"
    market_path = ROOT / "data/frozen/market-observations.json"
    final_path = ROOT / "results/final-decision.json"
    contexts = load_jsonl(contexts_path)
    if len(contexts) != 30:
        raise ValueError("Exactly 30 frozen contexts are required")

    bedrock = run_bedrock(
        contexts,
        ROOT / "data/cache/bedrock",
        ROOT / "results/bedrock-ledger.json",
    )
    write_stable(bedrock_path, bedrock)

    market = {
        asset_id: fetch_observations(
            asset_id,
            datetime.fromisoformat("2026-04-01T00:00:00+00:00"),
            datetime.fromisoformat("2026-06-20T00:00:00+00:00"),
        )
        for asset_id in PRICE_SERIES
    }
    market_path.parent.mkdir(parents=True, exist_ok=True)
    market_path.write_text(json.dumps(market, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = compute_report(
        contexts,
        json.loads(baselines_path.read_text(encoding="utf-8")),
        bedrock,
        market,
    )
    final_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "paired": report["paired"],
                "bedrock": report["bedrock"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
