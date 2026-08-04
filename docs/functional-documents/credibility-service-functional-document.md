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

Contributing edges are keyed by the full edge ID, `FACTOR->TARGET` (unconditional) or
`FACTOR|CONDITION->TARGET` (conditioned). Each `(factor, target, condition)` triple is a distinct edge
with its own Beta-Bernoulli counts, so evidence for a conditioned edge is never collapsed into the
unconditional one.

`TARGET` is normally the canonical asset id. When a prediction fired on an **inherited** industry edge
(the asset had no edge of its own for that factor/condition), the edge ID names the `AssetGroup`
instead, so the update lands on the industry prior that actually fired rather than creating a per-asset
edge that was never seeded. Consequence to be aware of: several listings in one group can contribute
evidence to the same group edge from one industry event, so that edge accumulates faster than a
company-specific one.

This allows a dissenting edge to receive evidence when it was right even if arbitration chose the other direction.

## 5a. Offline structure learning

Beyond online per-edge updates, Credibility owns a deterministic offline batch learner. It mines
historical `EventDetected` payloads against realized next-session price moves, aggregates by
`(factor, condition, polarity, asset)`, and upserts conditioned edges with data-derived
direction/weight and Beta-Bernoulli priors. It performs no prediction-time work, uses no LLM, and
reads `cleansing.*`/`market_data.*` read-only as an offline-analytics exception. Expert seeds remain
the prior; the learner only refines or adds edges via idempotent `MERGE`.

**Scheduling:** When the Credibility Service starts, APScheduler runs the learner automatically
every 24 hours (controlled by `CREDIBILITY_LEARNING_INTERVAL_HOURS`, default `24`). It also fires
once immediately on startup. This behaviour is enabled by default (`CREDIBILITY_LEARNING_ENABLED=true`)
and can be disabled to rely solely on manual runs.

**Manual trigger:** The learner can also be invoked directly as a one-shot CLI batch, independent of
the running service:

```bash
python -m credibility.learning.run
```

**Abnormal return filter:** A flat `min_samples` threshold would silently discard rare but
high-impact events (e.g. a merger announcement causing a single 10% move) because only one
observation exists in the lookback window. To capture these, the learner tags each sample as
abnormal before grouping:

1. For each affected asset, fetch the last `CREDIBILITY_LEARNING_VOLATILITY_LOOKBACK_DAYS` (default
   `30`) of closing prices from `market_data.close_observations` and compute the standard deviation
   of daily returns — the asset's historical daily volatility.
2. A sample is `is_abnormal=True` when `|actual_return| >= abnormal_threshold × volatility`, where
   `CREDIBILITY_LEARNING_ABNORMAL_THRESHOLD` defaults to `2.0` (the move must be at least 2× the
   asset's normal daily swing).
3. In the estimator, if **any** sample in a `(factor, condition, asset)` group is abnormal, the
   effective `min_samples` is reduced to `1`. Otherwise the configured `min_samples` (default `5`)
   applies unchanged.
4. When no volatility history is available (fewer than two closes), the sample is treated as normal
   — `is_abnormal=False`, `asset_volatility=0.0` — so the existing threshold still applies.

This keeps the commodity repeating-event path completely unchanged while allowing single rare
high-impact events to produce a conditioned edge when the magnitude is statistically unusual for
that asset.

| Env var | Default | Purpose |
|---|---|---|
| `CREDIBILITY_LEARNING_VOLATILITY_LOOKBACK_DAYS` | `30` | Days of closes used to compute per-asset historical volatility |
| `CREDIBILITY_LEARNING_ABNORMAL_THRESHOLD` | `2.0` | Multiplier above which a move is considered abnormal |

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
