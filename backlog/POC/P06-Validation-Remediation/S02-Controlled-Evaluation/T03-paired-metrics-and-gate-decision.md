# T03: Paired Metrics and Gate Decision

**Status:** COMPLETE - STOP

**Evidence:** [metrics.py](../../../../src/poc/poc6/metrics.py), [metrics tests](../../../../src/poc/poc6/test_metrics.py), and [final decision](../../../../src/poc/poc6/results/final-decision.json). KG-plus-LLM corrected 2 graph-only decisions, harmed 3, and recorded net -1 paired evidence.

## Requirements

- Join frozen predictions to the approved close observations only after all decisions are immutable.
- Report contexts corrected by KG-plus-LLM versus graph-only and contexts harmed.
- Report conflict-only balanced accuracy and macro-F1 when class support permits.
- Record confidence, Brier score, and intervals as descriptive evidence only.
- Report calls, tokens, retries, latency, cache hits, and list-price cost.
- Replay each scored prediction twice to verify duplicate-safe learning without changing comparison-time graph weights.
- Record one decision: `PASS`, `REVISE`, or `STOP`.

## Acceptance criteria

1. Every metric traces to frozen context, prediction, and price hashes.
2. The report clearly states the small-sample limitation.
3. `PASS` requires favorable paired evidence and no correctness/reproducibility failure.
4. `STOP` prevents M1 LLM-arbitration implementation unless a new hypothesis is approved.
