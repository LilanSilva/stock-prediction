# E08 - API Gateway / BFF

> **Not built.** Read [functional-document.md](functional-document.md) for this epic's design intent, and
> [requirements/](../../requirements/README.md) for the contracts it must consume — the task files below
> predate those and their conflicting details are non-authoritative.

## Overview

The API Gateway is a FastAPI-based Backend-for-Frontend (BFF) that serves as the single entry point for the React Dashboard. It reads from the single Postgres database (`feed`, six service-owned schemas), exposes REST endpoints for historical data, and maintains persistent WebSocket connections for real-time push updates.

The gateway does not perform any business logic or ML inference. Its sole responsibilities are:
1. Reading and shaping data from Postgres (predictions, scores, events, prices, credibility weights)
2. Subscribing to RabbitMQ queues and broadcasting live messages to connected Dashboard clients
3. Aggregating health status from all six backend microservices

## Architecture Context

**Service location:** `src/services/api-gateway/`

**Tables read (one Postgres database, read-only, schema-qualified):**
- `prediction.predictions` and `prediction.contributing_edges` - written by Prediction Service
- `verification.scores` and `verification.price_observations` - written by Verification Service
- `cleansing.events` - canonical events written by Cleansing Service
- `ingestion.articles` - normalised articles written by Ingestion Service
- `market_data.close_observations` - closing prices written by Market Data Service
- `credibility.credibility` - current alpha/beta weights written by Credibility Service
- `credibility.credibility_history` - time-series of weight changes

Confirm every table against the owning service's SRS section 9 before implementing — the list above is
design intent, not a verified schema.

**RabbitMQ queues consumed (dedicated live queues, never a service's work queue):**
- `gateway.predictions.live` - bound to `prediction.made`, for WebSocket broadcast
- `gateway.scored.live` - bound to `prediction.scored`, for WebSocket broadcast

**RabbitMQ queues published:** None (read-only consumer)

**Outbound HTTP calls:**
- Health endpoints on all six backend services (for `/health` aggregation)

**Shared library:** `src/shared/` package providing Pydantic message schemas, RabbitMQ client wrapper, and logging utilities

## Stories

| Story | Name | Description |
|-------|------|-------------|
| S01 | Core REST API Endpoints | All REST endpoints the Dashboard needs: predictions, credibility, events, prices, health |
| S02 | WebSocket Live Updates | Real-time push to Dashboard when new predictions or scores arrive via RabbitMQ |

## Overall Acceptance Criteria

1. All REST endpoints return correctly shaped JSON matching the documented response schemas
2. Pagination (limit/offset) works correctly on all list endpoints
3. Filters (date range, asset, direction, status) correctly narrow result sets
4. WebSocket clients receive `PredictionMade` and `PredictionScored` events within 500 ms of the message arriving on the RabbitMQ queue
5. WebSocket disconnections are handled gracefully without crashing the server or leaking connection handles
6. `/health` endpoint correctly reflects degraded state when any backend service is unreachable
7. All endpoints return HTTP 404 with a clear message for unknown IDs
8. Service starts cleanly via `docker compose up` and passes all pytest tests
9. Code passes `ruff` linting and `mypy --strict` type checking with zero errors
10. No business logic lives in the gateway - it only shapes and forwards data
