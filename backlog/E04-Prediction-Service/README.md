# E04 - Prediction Service

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

## Overview

The Prediction Service is the graph reasoning core of the news-driven market prediction pipeline. In M1 it consumes `EventDetected` messages, groups distinct events by canonical asset/context window, queries Neo4j for causal edges, and produces graph-only directional predictions. Prediction-time LLM arbitration is deferred after the POC-6 `STOP` result.

The service writes predictions to Postgres and publishes `PredictionMade`. Verification is the sole owner of publishing `PriceRequested`.

## Stories

| Story | Name | Description |
|---|---|---|
| S01 | Knowledge Graph Setup & Seeding | Neo4j schema, Cypher queries, and initial seed data (~30 causal edges) covering GOLD, OIL, USD, OMXS30, SPX |
| S02 | Prediction Engine | Multi-event context aggregation, graph-only prediction, prediction storage and publishing |

## Architecture Context

### Service Location
`services/prediction/`

### Queues
| Queue | Direction | Message |
|---|---|---|
| `events` | Consume | `EventDetected` |
| `predictions` | Publish | `PredictionMade` |

### Databases
| Database | Usage |
|---|---|
| Neo4j | Read causal subgraph; Credibility Service writes updated weights back here |
| Postgres (`predictions` table) | Store predictions with status, provenance, contributing edges |

### Upstream Dependencies
- Cleansing Service must publish `EventDetected` to the `events` queue
- Neo4j must be running with the schema and seed data from S01
- `src/shared/` library must expose: `EventDetected` and `PredictionMade` Pydantic schemas plus the RabbitMQ client wrapper. Prediction does not depend on the LLM gateway in M1.

### Downstream Consumers
- Verification Service consumes `predictions` queue
- Credibility Service writes learned weights back to Neo4j, which feeds future predictions

## Overall Acceptance Criteria

1. Service starts, connects to RabbitMQ `events` queue, and processes `EventDetected` messages without errors.
2. For every consumed event, the service queries Neo4j and returns zero or more firing causal edges.
3. For each asset with at least one material firing edge, a graph-only prediction is produced with valid direction, magnitude, confidence, and rationale fields.
4. Every prediction is persisted to the `predictions` Postgres table with status `PENDING`.
5. Every prediction triggers a `PredictionMade` message only; Verification later publishes `PriceRequested`.
6. If Neo4j returns no firing edges for an event, no prediction is produced and no messages are published.
7. Graph/query failures are caught, logged, and do not crash the service.
8. The Neo4j seed script populates at least 30 causal AFFECTS edges covering GOLD, OIL, USD, OMXS30, and SPX.
9. All unit tests pass under `pytest`; ruff and mypy report zero errors.
