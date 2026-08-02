# Credibility Service Functional Document

## 1. Purpose

The Credibility Service consumes scored predictions and updates learned evidence exactly once. Edge directional validity, arbiter decision quality, and source predictive utility are separate targets and must never be collapsed into one generic credibility score.

## 2. Inputs and ownership

- Consumes `credibility.scored`, bound to `prediction.scored`.
- Owns PostgreSQL schema `credibility`.
- Updates permitted learned properties on Neo4j causal edges.
- Publishes no required domain event in the initial POC.

## 3. Mandatory idempotency

Before any learning update, insert `prediction_id` into a processed-prediction ledger under a unique constraint. The ledger insertion and PostgreSQL learning changes occur atomically. If the prediction is already present, acknowledge it without updating any counter.

Where a graph update cannot share that transaction, use a durable graph-update outbox with a deterministic operation ID. Neo4j updates must record applied operation IDs or otherwise be safely reconcilable.

Correlated predictions from the same asset and context must not be counted as independent duplicate evidence.

## 4. Common prior

Every Beta-Bernoulli target begins with:

```text
alpha = 1.0
beta = 1.0
mean = alpha / (alpha + beta)
```

No alternative `2/2`, `7/3`, or source-specific prior is used in the POC.

## 5. Edge directional validity

Each contributing edge is judged against the actual market direction represented by that edge, not merely against whether the final prediction was correct.

- If the edge direction equals actual direction, increment alpha by its bounded evidence contribution.
- Otherwise increment beta by that contribution.
- Store evidence operation, before/after state, and score provenance.

Contributing edges are keyed by the full edge ID, `FACTOR->ASSET` (unconditional) or
`FACTOR|CONDITION->ASSET` (conditioned). Each `(factor, asset, condition)` triple is a distinct edge
with its own Beta-Bernoulli counts, so evidence for a conditioned edge is never collapsed into the
unconditional one.

This allows a dissenting edge to receive evidence when it was right even if arbitration chose the other direction.

## 5a. Offline structure learning

Beyond online per-edge updates, Credibility owns a deterministic offline batch learner
(`python -m credibility.learning.run`). It mines historical `EventDetected` payloads against realized
next-session price moves, aggregates by `(factor, condition, polarity, asset)`, and upserts
conditioned edges with data-derived direction/weight and Beta-Bernoulli priors. It is not an
always-on service, performs no prediction-time work, uses no LLM, and reads `cleansing.*`/
`market_data.*` read-only as an offline-analytics exception. Expert seeds remain the prior; the
learner only refines or adds edges via idempotent `MERGE`.

## 6. Arbiter decision quality

This target is deferred for M1 after the POC-6 `STOP` result. If a future approved experiment emits `decision_method=LLM_ARBITRATED`, update this target only for those predictions.

- Correct final direction increments alpha.
- Incorrect final direction increments beta.
- Store model and prompt version as dimensions so different arbitration policies are not silently mixed.

Graph-only predictions do not update arbiter quality.

## 7. Predictive source utility

Sources contributing to the event context receive bounded, normalized evidence based on the scored prediction. This metric is named `predictive_source_utility`; it is not factual truthfulness, journalistic reliability, or a moderation label.

Source contributions within one context are normalized so multiple articles about the same event do not multiply total evidence. Provenance records the context, event IDs, source IDs, and contribution rule.

## 8. Confidence and calibration

Store prediction confidence from the first observation. Do not add LLM calls or online confidence rewriting. Once sample size is sufficient, derive Brier score, calibration error, and reliability reports as offline/read-model calculations.

## 9. Failure and recovery

- Invalid score contracts and unsupported versions go to DLQ.
- PostgreSQL or Neo4j transient failures use bounded retry.
- Graph-update outbox operations are resumed after restart.
- A message is acknowledged only when the ledger and required recoverable work are durable.
- Partial updates are observable and reconciled; they are never silently ignored.

## 10. Operations

- `/health` reports process liveness.
- `/ready` checks PostgreSQL, Neo4j, and RabbitMQ.
- Shutdown drains or safely requeues in-flight messages and closes connections.
- Structured logs contain prediction/context/message IDs, target type, operation ID, and status.

## 11. Acceptance criteria

1. Replaying the same `PredictionScored` never changes counters twice.
2. All new Beta states begin at `1.0/1.0`.
3. Edge evidence is based on each edge direction relative to actual direction.
4. Arbiter quality updates only for LLM-arbitrated predictions.
5. Source utility is separately named, stored, and normalized per context.
6. Confidence is recorded without causing another LLM call.
7. A simulated Neo4j outage leaves a durable operation that completes after recovery.
