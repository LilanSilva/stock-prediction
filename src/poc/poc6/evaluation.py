"""Deterministic P06 baselines and hard-budget Bedrock evaluation runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MODEL_ID = "eu.anthropic.claude-sonnet-4-6"
GRAPH_VERSION = "p06-graph-v1"
PROMPT_VERSION = "p06-arbiter-v1"
WEIGHTS = {
    "military_conflict": {"GOLD": 0.70, "BRENT_OIL": 0.60},
    "strait_closure": {"GOLD": -0.50, "BRENT_OIL": 0.90},
    "supply_disruption": {"BRENT_OIL": 0.90},
    "sanctions": {"GOLD": 0.55, "BRENT_OIL": 0.50},
    "rate_hike": {"GOLD": -0.60},
    "rate_cut": {"GOLD": 0.60},
    "inflation_rise": {"GOLD": 0.65},
    "inflation_fall": {"GOLD": -0.55},
    "recession_signal": {"GOLD": 0.50, "BRENT_OIL": -0.40},
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def graph_decision(context: dict[str, Any]) -> dict[str, Any]:
    asset = context["asset_id"]
    edges: list[dict[str, Any]] = []
    score = 0.0
    total = 0.0
    for signal in context["signals"]:
        weight = WEIGHTS.get(signal["rule"], {}).get(asset)
        if weight is None:
            continue
        score += weight
        total += abs(weight)
        edges.append(
            {
                "event_id": signal["event_id"],
                "rule": signal["rule"],
                "weight": weight,
            }
        )
    direction = "UP" if score > 0.10 else "DOWN" if score < -0.10 else "NEUTRAL"
    strength = abs(score)
    magnitude = "SMALL" if strength < 0.60 else "MEDIUM" if strength < 1.20 else "LARGE"
    confidence = round(0.5 if not total else 0.5 + 0.4 * min(1.0, strength / total), 4)
    return {
        "context_id": context["context_id"],
        "context_hash": context["context_hash"],
        "asset_id": asset,
        "algorithm": "GRAPH_ONLY",
        "algorithm_version": GRAPH_VERSION,
        "direction": direction,
        "magnitude": magnitude,
        "confidence": confidence,
        "signed_score": round(score, 6),
        "contributing_edges": edges,
    }


def build_baselines(contexts: list[dict[str, Any]]) -> dict[str, Any]:
    graph = [graph_decision(context) for context in contexts]
    neutral = [
        {
            "context_id": item["context_id"],
            "context_hash": item["context_hash"],
            "asset_id": item["asset_id"],
            "algorithm": "ALWAYS_NEUTRAL",
            "algorithm_version": "p06-neutral-v1",
            "direction": "NEUTRAL",
            "magnitude": "SMALL",
            "confidence": 1 / 3,
        }
        for item in contexts
    ]
    majority = [
        {
            "context_id": item["context_id"],
            "context_hash": item["context_hash"],
            "asset_id": item["asset_id"],
            "algorithm": "PREDECLARED_MAJORITY",
            "algorithm_version": "p06-majority-v1",
            "direction": "UP",
            "magnitude": "SMALL",
            "confidence": 1 / 3,
            "declaration": "UP fixed before outcome join; not learned from evaluation outcomes",
        }
        for item in contexts
    ]
    return {
        "graph_fixture_version": GRAPH_VERSION,
        "weights": WEIGHTS,
        "outcomes_loaded": False,
        "graph_only": graph,
        "always_neutral": neutral,
        "predeclared_majority": majority,
    }


@dataclass
class EvaluationBudget:
    max_calls: int = 40
    max_input_tokens: int = 80_000
    max_output_tokens: int = 8_000
    calls: int = 0
    reserved_input_tokens: int = 0
    reserved_output_tokens: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0

    def reserve(self, input_tokens: int, output_tokens: int) -> None:
        if self.calls + 1 > self.max_calls:
            raise RuntimeError("call budget exceeded")
        if self.reserved_input_tokens + input_tokens > self.max_input_tokens:
            raise RuntimeError("input-token budget exceeded")
        if self.reserved_output_tokens + output_tokens > self.max_output_tokens:
            raise RuntimeError("output-token budget exceeded")
        self.calls += 1
        self.reserved_input_tokens += input_tokens
        self.reserved_output_tokens += output_tokens

    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.provider_input_tokens += input_tokens
        self.provider_output_tokens += output_tokens

    @property
    def maximum_list_price_usd(self) -> float:
        return round(
            self.reserved_input_tokens / 1_000_000 * 3
            + self.reserved_output_tokens / 1_000_000 * 15,
            6,
        )


def compact_context(context: dict[str, Any], include_graph: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "asset_id": context["asset_id"],
        "window_start": context["window_start"],
        "events": [
            {
                "event_type": item["event_type"],
                "direction": item["direction"],
                "title": item["title"][:180],
            }
            for item in context["signals"]
        ],
    }
    if include_graph:
        payload["graph_edges"] = graph_decision(context)["contributing_edges"]
    return payload


def prompt_for(context: dict[str, Any], mode: str) -> str:
    include_graph = mode == "KG_LLM"
    return (
        "Decide the next trading-session direction for this asset from only the supplied "
        "concurrent events. Resolve opposing forces. Return JSON only with keys direction "
        "(UP|DOWN|NEUTRAL), magnitude (SMALL|MEDIUM|LARGE), confidence (0..1), and rationale "
        "(max 35 words). Do not invent facts or edges.\n"
        + json.dumps(compact_context(context, include_graph), ensure_ascii=False, separators=(",", ":"))
    )


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 4)


def dry_run(contexts: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [
        {"mode": "KG_LLM", "context": context, "prompt": prompt_for(context, "KG_LLM")}
        for context in contexts
    ] + [
        {"mode": "LLM_ONLY", "context": context, "prompt": prompt_for(context, "LLM_ONLY")}
        for context in contexts[:10]
    ]
    if len(attempts) != 40:
        raise ValueError(f"Expected exactly 40 attempts, found {len(attempts)}")
    estimates = [estimate_tokens(item["prompt"]) for item in attempts]
    if max(estimates) > 1_200:
        raise ValueError("A prompt exceeds the 1,200-token target")
    budget = EvaluationBudget()
    for estimate in estimates:
        budget.reserve(estimate, 120)
    return {
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "attempts": [
            {
                "mode": item["mode"],
                "context_id": item["context"]["context_id"],
                "context_hash": item["context"]["context_hash"],
                "estimated_input_tokens": estimate,
                "maximum_output_tokens": 120,
            }
            for item, estimate in zip(attempts, estimates)
        ],
        "budget": asdict(budget),
        "maximum_list_price_usd": budget.maximum_list_price_usd,
        "llm_calls_made": 0,
    }


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    value = json.loads(cleaned)
    if value.get("direction") not in {"UP", "DOWN", "NEUTRAL"}:
        raise ValueError("Invalid direction")
    if value.get("magnitude") not in {"SMALL", "MEDIUM", "LARGE"}:
        raise ValueError("Invalid magnitude")
    confidence = float(value.get("confidence"))
    if not 0 <= confidence <= 1:
        raise ValueError("Invalid confidence")
    value["confidence"] = confidence
    value["rationale"] = str(value.get("rationale", ""))[:240]
    return value


def invoke_bedrock(prompt: str) -> dict[str, Any]:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 120,
        "temperature": 0,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        request_path = root / "request.json"
        response_path = root / "response.json"
        request_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        command = [
            "aws",
            "bedrock-runtime",
            "invoke-model",
            "--model-id",
            MODEL_ID,
            "--content-type",
            "application/json",
            "--accept",
            "application/json",
            "--body",
            f"fileb://{request_path}",
            "--cli-binary-format",
            "raw-in-base64-out",
            str(response_path),
            "--no-cli-pager",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip()[:500])
        return json.loads(response_path.read_text(encoding="utf-8"))


def run_bedrock(
    contexts: list[dict[str, Any]], cache_dir: Path, ledger_path: Path
) -> dict[str, Any]:
    attempts = [("KG_LLM", context) for context in contexts] + [
        ("LLM_ONLY", context) for context in contexts[:10]
    ]
    if len(attempts) != 40:
        raise ValueError("The controlled run requires exactly 30 + 10 attempts")
    budget = EvaluationBudget()
    results: list[dict[str, Any]] = []
    cache_dir.mkdir(parents=True, exist_ok=True)

    for mode, context in attempts:
        prompt = prompt_for(context, mode)
        cache_key = hashlib.sha256(
            f"{MODEL_ID}|{PROMPT_VERSION}|{mode}|{context['context_hash']}".encode("utf-8")
        ).hexdigest()
        cache_path = cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["cache_hit"] = True
            results.append(cached)
            continue

        budget.reserve(estimate_tokens(prompt), 120)
        try:
            response = invoke_bedrock(prompt)
            usage = response.get("usage") or {}
            parsed = parse_model_json(response["content"][0]["text"])
            budget.record_usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
            item = {
                "context_id": context["context_id"],
                "context_hash": context["context_hash"],
                "asset_id": context["asset_id"],
                "mode": mode,
                "model_id": MODEL_ID,
                "prompt_version": PROMPT_VERSION,
                "prediction": parsed,
                "usage": usage,
                "response_hash": hashlib.sha256(
                    json.dumps(response, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "cache_hit": False,
                "status": "OK",
            }
            cache_path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            results.append(item)
        except Exception as error:
            results.append(
                {
                    "context_id": context["context_id"],
                    "context_hash": context["context_hash"],
                    "asset_id": context["asset_id"],
                    "mode": mode,
                    "status": "ERROR",
                    "error": type(error).__name__,
                    "detail": str(error)[:500],
                }
            )
            break
        finally:
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(json.dumps(asdict(budget), indent=2) + "\n", encoding="utf-8")

    return {
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "budget": asdict(budget),
        "maximum_list_price_usd": budget.maximum_list_price_usd,
        "results": results,
    }


def write_stable(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run P06 baselines or capped Bedrock evaluation")
    parser.add_argument("command", choices=("baselines", "dry-run", "run"))
    parser.add_argument("--contexts", type=Path, default=Path("data/frozen/conflict-contexts.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/bedrock"))
    parser.add_argument("--ledger", type=Path, default=Path("results/bedrock-ledger.json"))
    args = parser.parse_args(argv)
    contexts = load_jsonl(args.contexts)
    if args.command == "baselines":
        result = build_baselines(contexts)
    elif args.command == "dry-run":
        result = dry_run(contexts)
    else:
        result = run_bedrock(contexts, args.cache_dir, args.ledger)
    write_stable(args.output, result)
    print(json.dumps({"command": args.command, "contexts": len(contexts), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
