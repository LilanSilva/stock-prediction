# T02: Deferred LLM Conflict Arbiter

> **POC-6 finding override (2026-07-13):** This task is not part of M1. POC-6 recorded `STOP` for KG-plus-LLM arbitration because KG-plus-LLM underperformed graph-only on the frozen 30-context sample.

## Current Decision

Do not implement prediction-time LLM arbitration in M1.

M1 Prediction Service must use graph-only decision logic. The service must not call `shared.llm.LLMGateway`.

## Future Use

This task may be reopened only if a new controlled hypothesis is approved. If reopened, it must use the shared provider-configurable LLM gateway instead of importing any provider SDK directly.

Future arbitration requirements:

- Call only through `shared.llm.LLMGateway`.
- Select provider/model through `LLM_PROVIDER` and `LLM_MODEL`.
- Use provider-specific API keys or cloud credentials through configuration.
- Send compact, versioned multi-event asset context plus canonical graph paths.
- Cache by provider, model, prompt version, schema hash, and context hash.
- Record provider, model, prompt version, input/output tokens, latency, attempt count, status, and response hash.
- Retry malformed structured output at most once.
- Count every attempt against the approved experiment budget.

## M1 Replacement

Implement graph-only decision policy instead:

- Sum or otherwise combine signed graph forces using the frozen graph policy.
- Produce `PredictionMade` with `decision_method=GRAPH_ONLY`.
- Store contributing edges and rationale.
- Publish only `prediction.made`.
- Do not publish `price.requested`.
