# S02: Scoring Engine

## Overview

This story implements the core scoring logic of the Verification Service. Once a prediction's time window has closed and the actual close price is available, the scoring engine computes the actual return (close-to-close), applies a ±0.3% deadband to determine the actual direction, compares it to the predicted direction to produce `is_correct`, persists the result, and publishes a `PredictionScored` message to the `scored-predictions` queue for consumption by the Credibility Service.

## Tasks

| ID  | Name                              | Description                                                                                                             |
|-----|-----------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| T01 | Close-to-close scoring with deadband | Pure scoring logic: compute actual_return, apply deadband, determine actual_direction, set is_correct, persist to outcomes. |
| T02 | PredictionScored publisher        | Build and publish `PredictionScored` message, update outcomes status, emit accuracy metric.                              |

## Dependencies

Before this story can start:
- **S01/T01 must be complete:** The `outcomes` and `price_observations` tables must exist and be populated.
- The `src/shared/` library must expose the `PredictionScored` Pydantic schema.
- The `scored-predictions` queue must be declared in RabbitMQ.
- The `prices` queue consumer (stub from S01/T01) must be writing `PriceObservation` rows.

## How to Test End-to-End

1. Start the full stack via `docker-compose up`.
2. Publish a synthetic `PredictionMade` to `predictions` with `direction = UP`, `time_horizon = 1d`.
3. Confirm `outcomes` row has `status = PENDING`.
4. Publish a synthetic `PriceObserved` to `prices` with `close` value implying an actual return > +0.3% (e.g. baseline close 100.0, window close 100.5 = +0.5%).
5. Wait for or manually trigger the window-close job.
6. Confirm `outcomes` row has `status = SCORED`, `actual_direction = UP`, `is_correct = true`.
7. Confirm a `PredictionScored` message appears in the `scored-predictions` queue with all required fields.
8. Repeat with an actual return of +0.1% (within deadband) — confirm `actual_direction = NEUTRAL`, `is_correct = false` (since predicted UP).
9. Check metrics endpoint or logs for updated `prediction_accuracy_rate`.
