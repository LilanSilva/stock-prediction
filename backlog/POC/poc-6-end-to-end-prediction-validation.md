# POC-6: End-to-End Prediction Validation

**Status:** COMPLETE - STOP  
**Gate run:** 2026-07-13  
**LLM usage:** 40 calls, 10,762 input tokens, 3,357 output tokens, USD 0.082641 list-price cost  
**Decision:** Do not implement prediction-time KG-plus-LLM arbitration for M1. Build the walking skeleton with graph-only prediction unless a new controlled hypothesis is approved.

## Final Controlled Rerun Result

P06 repaired the earlier dataset and market-data gates, then reran POC-6 on 30 frozen conflict contexts. Context selection, graph weights, prompts, baselines, and market policy were frozen before outcome prices were joined.

The final result is `STOP`: KG-plus-LLM did not improve enough over graph-only to justify its implementation cost and complexity.

| Method | Contexts | Accuracy | Balanced accuracy | Macro-F1 |
|---|---:|---:|---:|---:|
| Graph-only | 30 | 40.0% | 40.9% | 30.4% |
| KG-plus-LLM | 30 | 36.7% | 32.5% | 21.5% |
| LLM-only diagnostic | 10 | 40.0% | 26.7% | 19.0% |
| Always neutral | 30 | 13.3% | 33.3% | 7.8% |
| Predeclared majority | 30 | 36.7% | 33.3% | 17.9% |

Paired KG-plus-LLM evidence versus graph-only: 2 corrected, 3 harmed, net -1.

## Executable Evidence

- Harness: [src/poc/poc6/poc6.py](../../src/poc/poc6/poc6.py)
- P06 completion runner: [src/poc/poc6/complete_p06.py](../../src/poc/poc6/complete_p06.py)
- Final machine-readable decision: [src/poc/poc6/results/final-decision.json](../../src/poc/poc6/results/final-decision.json)
- Bedrock evaluation ledger: [src/poc/poc6/results/bedrock-evaluation.json](../../src/poc/poc6/results/bedrock-evaluation.json)
- Market observations: [src/poc/poc6/data/frozen/market-observations.json](../../src/poc/poc6/data/frozen/market-observations.json)
- Frozen reviewed contexts: [src/poc/poc6/data/frozen/conflict-contexts.jsonl](../../src/poc/poc6/data/frozen/conflict-contexts.jsonl)

## Source And Context Findings

The first zero-token run found no qualifying context corpus. P06 remediation then built a reproducible historical corpus from GDELT article metadata, froze 4,586 unique rows, reviewed 33 candidates, and accepted 30 conflict contexts. Review decisions were made before price outcomes were joined.

The corpus remains exploratory: 30 conflict contexts are useful for a POC gate, not production-grade accuracy proof.

## Market-Data Findings

P06 approved a POC-only Yahoo reference-close policy:

- `GOLD`: Yahoo `GC=F`, COMEX/CMX metadata, USD future.
- `BRENT_OIL`: Yahoo `BZ=F`, NYM metadata for Brent Crude Oil Last Day Financial, USD future.
- Values are `PROVIDER_DAILY_CLOSE`, raw/unadjusted, not official exchange settlements.
- Continuous futures use the provider-managed include-all rollover policy for this POC.
- Stooq remains unvalidated and is not enabled as fallback.

## Original Hypothesis

For genuinely conflicting concurrent news contexts, conditional KG-plus-LLM arbitration should improve directional decisions over a deterministic graph-only weighted vote without exceeding the fixed LLM budget.

This hypothesis was not supported by the controlled sample.

## Implementation Implication

M1 should not build `LLM_ARBITRATED` prediction behavior from this hypothesis. It may proceed with graph-only prediction after the remaining contract-freeze work, while keeping LLM arbitration as a deferred experimental option.

## Metrics And Interpretation

Primary evidence:

- Paired count where KG-plus-LLM corrects graph-only.
- Paired count where KG-plus-LLM harms graph-only.
- Conflict-only balanced accuracy and macro-F1 when every class has support.
- Calls, tokens, latency, structured-output failures, and cost.

Confidence and Brier score are recorded but are not pass criteria with this small sample. The rerun remains exploratory and may justify a larger blinded evaluation; it cannot establish production-grade accuracy.

## Rerun Outcomes

- `PASS`: evidence supports building the narrow walking skeleton with LLM arbitration.
- `REVISE`: data, graph, prompt, or market policy needs another controlled iteration.
- `STOP`: conditional LLM arbitration does not improve enough over graph-only to justify its complexity.
