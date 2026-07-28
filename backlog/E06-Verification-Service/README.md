# E06: Verification Service

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

## Overview

The Verification Service is responsible for closing the prediction feedback loop. It consumes incoming predictions, tracks their time windows, waits for actual price data to arrive, scores each prediction against the real market outcome, and publishes a scored result downstream to the Credibility Service.

This service is the bridge between the forward-looking Prediction Service and the backward-looking Credibility Service. Without it, there is no signal to update causal edge weights in the knowledge graph.

## Stories

| ID  | Name                                    | Description                                                                                      |
|-----|-----------------------------------------|--------------------------------------------------------------------------------------------------|
| S01 | Prediction Tracking & Window Management | Store incoming predictions, manage time windows, trigger price requests when windows close.       |
| S02 | Scoring Engine                          | Compare prediction vs actual using deadband logic, mark correct/wrong, publish scored result.     |

## Architecture Context

**Service:** `src/services/verification/`

**Consumes from queues:**
- `predictions` — `PredictionMade` messages from the Prediction Service
- `prices` — `PriceObserved` messages from the Market Data Service

**Publishes to queues:**
- `price-requests` — `PriceRequested` messages when a window closes and no price is yet available
- `scored-predictions` — `PredictionScored` messages after scoring is complete

**Database:** Postgres (shared instance, `verification` schema or dedicated `outcomes` table)
- Table: `outcomes` — stores prediction state, window deadline, score, actual return

**No Neo4j access** — the Verification Service is purely Postgres + RabbitMQ.

## Pipeline Position

```
Prediction Service
       |
  [predictions queue]  [prices queue]
       |                     |
  Verification Service ------+
       |
  [price-requests queue] (re-request if price missing)
       |
  [scored-predictions queue]
       |
  Credibility Service
```

## Overall Acceptance Criteria

1. Every `PredictionMade` message consumed from the `predictions` queue results in a row in the `outcomes` table with `status = PENDING`.
2. When a prediction window closes, the service either finds an existing `PriceObserved` record or republishes a `PriceRequested` message.
3. Scoring applies a ±0.3% deadband: moves within the band are classified as `NEUTRAL` regardless of predicted direction.
4. `is_correct` is `True` if and only if `predicted_direction == actual_direction`.
5. Every scored prediction results in a `PredictionScored` message published to the `scored-predictions` queue with all required fields populated.
6. The `outcomes` table row is updated to `status = SCORED` after publishing.
7. A rolling 24-hour `prediction_accuracy_rate` metric is emitted (Prometheus or structured log).
8. All code passes `ruff` linting and `mypy` type checking with no errors.
9. Unit test coverage covers scoring logic, deadband edge cases, and publisher output.
