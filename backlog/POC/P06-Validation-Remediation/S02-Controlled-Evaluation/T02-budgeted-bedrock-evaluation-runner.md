# T02: Budgeted Bedrock Evaluation Runner

**Status:** COMPLETE

**Evidence:** [complete_p06.py](../../../../src/poc/poc6/complete_p06.py), [Bedrock evaluation](../../../../src/poc/poc6/results/bedrock-evaluation.json), and [Bedrock ledger](../../../../src/poc/poc6/results/bedrock-ledger.json). The final run used 40 calls, 10,762 input tokens, 3,357 output tokens, and USD 0.082641 list-price cost.

## Requirements

- Use `anthropic.claude-sonnet-4-6` through the approved EU Bedrock inference path.
- Run KG-plus-LLM on 30 contexts and LLM-only on a frozen 10-context subset.
- Send compact structured events/edges, never full articles.
- Use strict structured output and a short bounded rationale.
- Count every attempt, including malformed-output retries, against 40 calls, 80,000 input tokens, and 8,000 output tokens.
- Target at most 1,200 input and 120 output tokens per attempt.
- Stop before a call that could exceed a hard limit.
- Cache by model, prompt version, and context hash before issuing another request.
- Persist provider token usage, latency, status, and response hash.

## Acceptance criteria

1. A dry run shows the exact contexts and maximum possible spend without invoking Bedrock.
2. Re-running a completed context makes zero new calls.
3. A simulated retry consumes another call reservation.
4. Budget exhaustion stops cleanly before invocation.
5. Maximum theoretical list-price spend remains below the pre-run cap; the final reserved cap was USD 0.09771 and actual list-price cost was USD 0.082641.
