# T01: Frozen Graph and Zero-Call Baselines

**Status:** COMPLETE

**Evidence:** [evaluation.py](../../../../src/poc/poc6/evaluation.py), [baseline results](../../../../src/poc/poc6/results/baselines.json), and [evaluation tests](../../../../src/poc/poc6/test_evaluation.py).

## Requirements

- Freeze the causal-edge fixture and `alpha=1.0`, `beta=1.0` starting state before joining outcomes.
- Generate graph-only decisions for all retained contexts.
- Generate always-neutral and pre-declared majority baselines.
- Do not derive the majority class from the evaluation outcomes.
- Keep weights fixed throughout comparison; learning replay occurs only after scoring.
- Persist decisions with context hash and algorithm version.

## Acceptance criteria

1. Repeated runs produce byte-equivalent baseline decisions.
2. Zero LLM calls occur.
3. Every decision references all contributing event IDs and edges.
4. Outcome data cannot influence graph weights or thresholds.
