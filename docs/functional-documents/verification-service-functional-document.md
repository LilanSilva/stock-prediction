# Verification Service Functional Document

## 1. Purpose

The Verification Service is the sole owner of prediction evaluation scheduling and scoring. It converts a new `PredictionMade` into one dual-session `PriceRequested`, then scores the returned immutable closes and publishes `PredictionScored` exactly once.

## 2. Inputs, outputs, and ownership

- Consumes `verification.predictions`, bound to `prediction.made`.
- Consumes `verification.prices`, bound to `price.observed`.
- Publishes `price.requested` and `prediction.scored` on `feed.events`.
- Owns PostgreSQL schema `verification`.
- Uses canonical asset calendar/timezone data to resolve sessions.

Prediction does not publish price requests; Market Data does not score predictions.

## 3. Evaluation creation

For each new immutable prediction:

1. Idempotently persist its evaluation record.
2. Resolve `baseline_session` as the last completed canonical session available at `decision_at` without look-ahead.
3. Resolve `settlement_session` as the next session required by `ONE_TRADING_DAY`.
4. Create a deterministic request ID tied to the prediction and scoring-contract version.
5. Add one canonical `PriceRequested` containing both sessions to the outbox.
6. Reconcile publication and retain pending state until both closes arrive.

Session resolution uses the asset registry calendar and timezone, including holidays and non-trading days.

When a `PredictionMade` carries `supersedes_prediction_id` (a market-closed stance collapse from
Prediction), withdraw the superseded prediction's evaluation so it is never scored. A `PriceObserved`
that arrives for a withdrawn evaluation is acknowledged without producing a `PredictionScored`.

## 4. Scoring

After a matching `PriceObserved` is validated against the evaluation's frozen registry version, price kind, session policy, adjustment flag, and rollover policy:

```text
actual_return = (settlement_close - baseline_close) / baseline_close
```

Direction:

- `abs(actual_return) < 0.003` -> `NEUTRAL`.
- Otherwise `actual_return > 0` -> `UP`.
- Otherwise -> `DOWN`.

Magnitude:

- Below 1% absolute return -> `SMALL`.
- 1% to below 3% -> `MEDIUM`.
- 3% or greater -> `LARGE`.
- Deadband outcomes are `SMALL`.

`is_correct` is true only when predicted and actual directions match. Initial `score` is `1.0` for correct and `0.0` for incorrect; confidence is retained for later calibration analysis, not used to rewrite the observed direction.

## 5. Immutable audit record

Store prediction/context/asset IDs, predicted values, confidence, both close observations and their complete provider metadata, return, actual values, correctness, score, scoring-contract version, and score timestamp. The output includes contributing edges and source IDs received through prediction provenance.

A scored record is immutable. Correcting bad market data requires an explicit future correction workflow, not an in-place silent update.

## 6. Idempotency and reliability

- Unique evaluation per prediction ID.
- One deterministic price request identity per evaluation contract version.
- Unique score per prediction ID.
- Duplicate `PredictionMade` reuses the evaluation and request.
- Duplicate `PriceObserved` reuses the existing score and does not republish a new evidence identity.
- State changes and publications use an outbox or equivalent reconciliation mechanism.
- Invalid/mismatched asset, session, request, or close data are quarantined/DLQ'd.

## 7. Operations

- `/health` reports process liveness.
- `/ready` checks PostgreSQL, RabbitMQ, and calendar/registry availability.
- Startup reconciles unpublished price requests and scores.
- Shutdown stops both consumers, completes or safely requeues in-flight work, and closes pools.
- Logs contain prediction/request/message/correlation IDs and scoring-contract version.

## 8. Acceptance criteria

1. Verification is the only `PriceRequested` producer.
2. Each request includes baseline and settlement sessions resolved without look-ahead.
3. Scoring uses exactly the two observations returned in one `PriceObserved`.
4. Deadband, direction, magnitude, and correctness follow the fixed rules above.
5. Duplicate messages cannot create a second score or evidence event.
6. Both close observations, price kind, provider bar/fetch times, adjustment flag, registry version, and provenance are stored with every outcome.
7. Pending publications recover after a simulated crash.
