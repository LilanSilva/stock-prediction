# Prediction Service Functional Document

## 1. Purpose

The Prediction Service combines concurrent structured events with causal graph evidence to produce one explainable directional prediction per canonical asset and context version. It publishes `PredictionMade` only; it never publishes `PriceRequested`.

## 2. Inputs, outputs, and stores

- Consumes `prediction.events`, bound to `event.detected` on `feed.events`.
- Publishes `prediction.made` on `feed.events`.
- Owns the PostgreSQL `prediction` schema.
- Reads and traverses Neo4j causal edges.
- Uses canonical [message contracts](../contracts/message-contracts.md), [assets](../reference/asset-registry.md), and [event types](../reference/event-taxonomy.md).

## 3. Context aggregation

An `EventDetected` is not automatically a final prediction trigger. The service:

1. Validates canonical event and asset IDs.
2. Adds the event to each affected asset's event-time context.
3. Uses an initial configurable 15-minute window and watermark.
4. Retains distinct causal events rather than merging them.
5. Persists context ID, version, window start/end, watermark, event IDs, and asset ID.
6. Creates a new version for an eligible late event; previously published predictions stay immutable.

The prediction idempotency key is `(asset_id, context_window_start, ONE_TRADING_DAY, context_version)`. One prediction may supersede the prior version through `supersedes_prediction_id`.

## 4. Graph inference

For every event in a ready context:

- Match its canonical event type to graph nodes.
- Gate conditioned edges by the event's `context_tags` (e.g. `TRANSPORT_AFFECTED`): a conditioned
  edge fires only when its condition is active; unconditional edges always fire.
- Traverse only configured, bounded causal paths relevant to the asset.
- Convert retained paths into signed `UP`, `DOWN`, or `NEUTRAL` forces. When the contributing event
  `polarity` is `RESOLUTION` (de-escalation), invert the edge sign before summing.
- Derive `RISK_PREMIUM_ELEVATED` at decision time from recent price history (Market Data
  `GET /prices/recent`). A `RESOLUTION`-driven `DOWN` on an asset is suppressed unless the asset is
  elevated, so a de-escalation only predicts a drop when there is a premium to unwind.
- Include edge ID, direction, current weight, influence weight, and compact path provenance. The
  edge ID is `FACTOR->ASSET` (unconditional) or `FACTOR|CONDITION->ASSET` (conditioned).
- Ignore/quarantine unknown graph mappings rather than inventing edges.

Neo4j stores causal graph state only. Prediction context and published prediction records are stored in PostgreSQL.

## 5. Decision policy

### Graph-only path

When all material retained forces agree, compute direction, confidence, and magnitude deterministically and set `decision_method=GRAPH_ONLY`. No LLM call is permitted.

### Stance management

An **active prediction** is any prediction row with `status = 'PENDING'` — meaning it has not yet
been scored by Verification. The service always checks against the most recent PENDING prediction
for the asset (`ORDER BY decision_at DESC LIMIT 1`) before creating a new one. Once a prediction is
scored its status changes away from PENDING and it no longer participates in stance checks; a new
prediction for the same asset with the same direction and magnitude can then be created freely.

Predictions are managed as a per-asset stance, keyed on the market state:

- **Trading day (price available):** compare the new decision `(direction, magnitude)` against the
  asset's latest active (PENDING) prediction.
  - If they match exactly → **skip silently**: no new prediction row, no message published. The
    context is marked PREDICTED. This prevents duplicate noise when the same signal fires again
    without any new information.
  - If they differ (direction changed, magnitude changed, or no prior PENDING prediction exists) →
    **create a new independent prediction** with no `supersedes_prediction_id`. The prior prediction
    remains active and will be scored on its own. An asset may therefore hold multiple concurrent
    active predictions, each scored independently.
- **Market closed / price unavailable (weekend, holiday, fetch failure):** collapse to one active
  prediction per asset. The new prediction sets `supersedes_prediction_id` to the prior PENDING
  prediction's ID, and Verification withdraws that prior prediction (marks it WITHDRAWN, unscored)
  in the same transaction. This prevents an unbounded backlog of unscored stances accumulating over
  a weekend.

Market state is per asset, not global. `shared.calendar.is_trading_day` is evaluated on **that
asset's own market calendar** AND requires a reachable Market Data price. At 23:00 UTC on a Friday
a Stockholm listing is already closed (Saturday locally) while a New York listing is still in its
trading day, so the same sweep can add an independent prediction for one asset and collapse the
stance for another. An asset with no registry entry has no calendar and is treated as closed — the
conservative collapse path rather than guessing a market.

### OTHER event type

Articles classified as `EventType.OTHER` by Cleansing produce an `EventDetected` and enter a
context window, but `OTHER` has no `CausalFactor` node in Neo4j. The graph query returns zero
firing edges, the decision policy returns `None`, and no prediction is ever produced. This is
intentional: `OTHER` is a catch-all for events with no modelled causal path.

### Conflict path

After the POC-6 `STOP` result, prediction-time LLM arbitration is not part of M1. When material forces genuinely disagree in M1, the service uses graph-only policy and records the conflict evidence. `LLM_ARBITRATED` remains a deferred experimental path.

If a future approved experiment re-enables arbitration:

- Make at most one primary LLM call per asset/context version.
- Send compact structured events and relevant graph paths, never full articles.
- Require a strict structured response with direction, magnitude, confidence, and a bounded rationale.
- Retry once only when structured output is malformed.
- Cache by prompt version, model, and deterministic context hash.
- Set `decision_method=LLM_ARBITRATED` and attach provider token/latency metadata.

If arbitration cannot produce a valid result, record the failure and do not publish a fabricated prediction.

## 6. Persistence and publication

Persist the context, prediction, contributing edges, decision method, confidence, and LLM metadata. Add `PredictionMade` to a transactional outbox or equivalent reconciled publication mechanism. A duplicate input must not call the LLM again or create a second prediction identity.

`decision_at` is the UTC time the immutable prediction becomes available. The initial horizon is always `ONE_TRADING_DAY`.

## 7. Token controls

Configuration includes model, prompt version, maximum compact-input size, low output-token limit, request timeout, and cache retention. Logs and stored metadata include input/output tokens, latency, attempt count, status, and context hash. Capturing provider metadata causes no additional LLM request.

## 8. Failure and recovery

- Unsupported schema major versions and invalid canonical IDs go to DLQ with safe failure metadata.
- Transient Neo4j, PostgreSQL, RabbitMQ, and LLM transport failures use bounded retry.
- Pending contexts and outbox rows are recovered after restart.
- A message is acknowledged only after durable state necessary for recovery is committed.

## 9. Operations

- `/health` reports process liveness.
- `/ready` checks PostgreSQL, Neo4j, RabbitMQ, and required model/config availability.
- Shutdown stops consumption, completes or safely requeues in-flight work, persists context state, and closes connections.
- Structured logs contain message/context/correlation IDs and decision reason, but not full article text or prompts.

## 10. Acceptance criteria

1. Multiple distinct events in one asset window contribute to one versioned prediction context.
2. A duplicate event delivery cannot create duplicate context membership or prediction output.
3. Agreeing forces produce `GRAPH_ONLY` with zero LLM calls.
4. M1 conflicting forces do not call the LLM; any future arbitration experiment permits at most one primary call per asset/context version and one malformed retry.
5. Late events produce a new version and preserve prior predictions.
6. Every output matches `PredictionMade` and uses canonical IDs.
7. The service never publishes `PriceRequested`.
8. Outbox and context recovery survive a simulated restart.
9. On a trading day, a new decision with the same `(direction, magnitude)` as the asset's latest PENDING prediction produces no new row and no new message.
10. On a trading day, a new decision with a different direction or magnitude creates an independent prediction alongside any existing active predictions.
11. On a market-closed day, a new prediction supersedes and withdraws the prior PENDING prediction so only one active prediction remains per asset.
12. An `EventType.OTHER` event never produces a prediction (no CausalFactor node exists; zero firing edges → no decision).
