# API Gateway / BFF Functional Document

## 1. Purpose

The API Gateway is a read-only backend-for-frontend (BFF) for the local POC dashboard. It exposes stable REST responses and live updates without owning prediction, scoring, or credibility business logic.

## 2. Responsibilities

- Read service-owned views from the single PostgreSQL database.
- Read knowledge-graph projections from Neo4j.
- Consume dedicated live-update queues and fan messages out to connected dashboard clients.
- Translate internal records into versioned public response models.
- Provide health and readiness endpoints.

The Gateway must not write to another service's schema, update graph weights, score predictions, or consume another service's work queue.

## 3. Data and messaging

- PostgreSQL access is read-only and limited to explicitly granted views.
- Neo4j access is read-only.
- Live prediction queue: `gateway.predictions.live`, bound to `prediction.made` on `feed.events`.
- Live score queue: `gateway.scored.live`, bound to `prediction.scored` on `feed.events`.
- Acknowledgement occurs only after a message has been validated and handed to the connection manager.
- Duplicate messages are suppressed by `message_id` for a bounded retention period.

The canonical internal messages are defined in [message-contracts.md](../contracts/message-contracts.md). Public models use canonical `asset_id`, `event_type`, horizon, direction, and decision-method values.

## 4. Minimum REST surface

- `GET /api/v1/predictions`
- `GET /api/v1/predictions/{prediction_id}`
- `GET /api/v1/predictions/{prediction_id}/score`
- `GET /api/v1/events`
- `GET /api/v1/assets/{asset_id}/prices`
- `GET /api/v1/credibility/edges`
- `GET /api/v1/credibility/sources`
- `GET /api/v1/graph`
- `GET /health`
- `GET /ready`

List endpoints must use bounded pagination and deterministic ordering. Unknown identifiers return `404`; invalid filters return `422`.

## 5. Live-update surface

- `WS /api/v1/live`
- Clients may subscribe to `predictions` and/or `scores`.
- The server sends a small typed envelope containing `type`, `message_id`, `occurred_at`, and the canonical payload.
- Slow or disconnected clients must not block RabbitMQ consumption.

## 6. Security scope

For the POC, the Gateway binds to localhost and uses restrictive CORS for the local dashboard origin. It must not be exposed publicly. Public authentication, authorization, rate limiting, and internet-facing hardening are deferred until deployment scope changes.

## 7. Operational requirements

- `/health` reports process liveness without dependency calls.
- `/ready` checks PostgreSQL, Neo4j, and RabbitMQ connectivity.
- Shutdown stops accepting new connections, closes WebSockets, stops consumers, and closes dependency pools.
- Structured logs include correlation identifiers but never raw article bodies, prompts, secrets, or credentials.

## 8. Acceptance criteria

1. REST endpoints return canonical identifiers and stable versioned shapes.
2. The Gateway has read-only database and graph permissions.
3. Live queues are independent from Verification and Credibility work queues.
4. Duplicate live messages do not produce duplicate dashboard updates.
5. Health, readiness, and graceful shutdown work locally.
6. No prediction or learning rule is implemented in the Gateway.

