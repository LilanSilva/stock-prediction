# S01: Prediction Tracking & Window Management

## Overview

This story builds the intake and scheduling layer of the Verification Service. It consumes `PredictionMade` messages, persists them to Postgres with a calculated window deadline, and uses APScheduler to fire a check job when each window closes. If the price data has already arrived via the `prices` queue, the job hands off to the scoring engine; if not, it republishes a `PriceRequested` message to prompt the Market Data Service.

## Tasks

| ID  | Name                                      | Description                                                                                                          |
|-----|-------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| T01 | Prediction consumer & deadline scheduler  | Consume `PredictionMade`, persist to `outcomes` table, schedule APScheduler job at `window_close_at`, re-request price if missing. |

## Dependencies

Before this story can start:
- The `src/shared/` library must expose `PredictionMade`, `PriceRequested`, and `PriceObserved` Pydantic schemas.
- The `src/shared/` RabbitMQ client wrapper must be available (`shared.rabbitmq.RabbitMQClient`).
- Postgres must be reachable and the `outcomes` table migration must exist (or be created in this task).
- The `predictions` and `prices` queues must be declared in RabbitMQ.
- The `price-requests` queue must be declared in RabbitMQ.

## How to Test End-to-End

1. Start the full stack via `docker-compose up`.
2. Publish a synthetic `PredictionMade` message to the `predictions` queue with a `time_horizon` set 30 seconds in the future (for test speed).
3. Confirm a row appears in `outcomes` with `status = PENDING` and a correct `window_close_at`.
4. Wait for the APScheduler job to fire.
5. If no matching `PriceObserved` row exists: confirm a `PriceRequested` message appears in the `price-requests` queue.
6. Publish a synthetic `PriceObserved` message and confirm the job does NOT re-request.
