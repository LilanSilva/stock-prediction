# P06: POC-6 Validation Remediation

**Status:** COMPLETE - STOP

## Outcome

P06 produced a frozen, leakage-controlled evaluation corpus, an auditable Gold/Brent reference-price policy, and a capped Bedrock rerun. The rerun recorded `STOP`: KG-plus-LLM did not improve enough over graph-only to justify prediction-time LLM arbitration in M1.

This POC work package was the only implementation work authorized before the POC-6 gate decision. Its harness is disposable research code; it must not grow into the production microservices.

## Completed rerun evidence

- Final decision: [src/poc/poc6/results/final-decision.json](../../../src/poc/poc6/results/final-decision.json)
- Bedrock run: [src/poc/poc6/results/bedrock-evaluation.json](../../../src/poc/poc6/results/bedrock-evaluation.json)
- Market observations: [src/poc/poc6/data/frozen/market-observations.json](../../../src/poc/poc6/data/frozen/market-observations.json)
- Frozen contexts: [src/poc/poc6/data/frozen/conflict-contexts.jsonl](../../../src/poc/poc6/data/frozen/conflict-contexts.jsonl)

Measured result:

- Contexts: 30.
- Bedrock calls: 40; errors: 0.
- Tokens: 10,762 input and 3,357 output.
- Actual list-price cost: USD 0.082641.
- Graph-only accuracy: 40.0%; KG-plus-LLM accuracy: 36.7%.
- Paired evidence: 2 corrected, 3 harmed, net -1.

## Stories

| Story | Outcome |
|---|---|
| [S01 - Dataset and Market Ground Truth](S01-Dataset-Market-Ground-Truth/README.md) | Completed: 30 reviewed conflict contexts and validated Yahoo reference-close observations |
| [S02 - Controlled Baseline and LLM Evaluation](S02-Controlled-Evaluation/README.md) | Completed: reproducible zero-call baselines, capped Bedrock run, and `STOP` report |

## Exit criteria

1. Context selection is frozen before outcome prices are joined.
2. Gold and Brent reference series, sessions, and rollover policy are approved for POC scoring.
3. Every model attempt is cached and counted against one hard budget.
4. Results reproduce from stored fixtures without network calls.
5. POC-6 records `STOP` with paired evidence.
