# E08 - API Gateway / BFF

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

## Overview

The API Gateway is a FastAPI-based Backend-for-Frontend (BFF) that serves as the single entry point for the React Dashboard. It aggregates data from all Postgres databases, exposes REST endpoints for historical data, and maintains persistent WebSocket connections for real-time push updates.

The gateway does not perform any business logic or ML inference. Its sole responsibilities are:
1. Reading and shaping data from Postgres (predictions, scores, events, prices, credibility weights)
2. Subscribing to RabbitMQ queues and broadcasting live messages to connected Dashboard clients
3. Aggregating health status from all six backend microservices

## Architecture Context

**Service location:** `services/api-gateway/`

**Databases read (Postgres with pgvector):**
- `predictions` table - prediction records written by Prediction Service
- `prediction_outcomes` table - scored outcomes written by Verification Service
- `events` table - canonical events written by Cleansing Service
- `articles` table - raw articles written by Ingestion Service
- `prices` table - OHLC price observations written by Market Data Service
- `credibility_edges` table - current alpha/beta weights written by Credibility Service
- `credibility_sources` table - per-source credibility written by Credibility Service
- `credibility_edge_history` table - time-series of weight changes

**RabbitMQ queues consumed (read-only fan-out):**
- `predictions` queue - `PredictionMade` messages (subscribe for WebSocket broadcast)
- `scored-predictions` queue - `PredictionScored` messages (subscribe for WebSocket broadcast)

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
