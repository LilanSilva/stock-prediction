# E01: Shared Infrastructure & Foundation

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

## Overview

This epic establishes the complete local development environment and shared Python library that every other epic depends on. Nothing else can be built until this epic is complete.

It covers two things:
1. The infrastructure layer: Docker Compose orchestration of Postgres (with pgvector), Neo4j, and RabbitMQ, plus all initialization scripts and queue declarations.
2. The shared Python library (`src/shared/`) that every microservice imports: Pydantic message schemas, the async RabbitMQ client wrapper, a provider-configurable LLM gateway, and structured logging with correlation ID propagation.

## Stories

| Story | Description |
|---|---|
| S01 - Docker Compose & Infrastructure Setup | Bring up all databases and the message broker locally. Includes health checks, volumes, network, initialization SQL/Cypher scripts, and RabbitMQ queue declarations. |
| S02 - Shared Python Library | Build the `src/shared/` package: Pydantic v2 message schemas for all six queue events, async aio-pika wrapper, provider-configurable LLM gateway, and structlog JSON logging with correlation IDs. |

## Architecture Context

### Services and their infrastructure dependencies

All services share ONE Postgres database via service-owned schemas. Backend services publish to the
`feed.events` topic exchange by routing key and consume their own bound queue (never another
service's work queue).

| Service | Schema (in `feed` DB) | Consumes queue (routing key) | Publishes (routing key) |
|---|---|---|---|
| Ingestion | `ingestion` | — | `article.ingested` |
| Cleansing | `cleansing` | `cleansing.articles` (`article.ingested`) | `event.detected` |
| Prediction | `prediction` + Neo4j | `prediction.events` (`event.detected`) | `prediction.made` |
| Verification | `verification` | `verification.predictions` (`prediction.made`), `verification.prices` (`price.observed`) | `price.requested`, `prediction.scored` |
| Market Data | `market_data` | `market-data.price-requests` (`price.requested`) | `price.observed` |
| Credibility | `credibility` + Neo4j | `credibility.scored` (`prediction.scored`) | — |
| API Gateway | read-only views + Neo4j | `gateway.predictions.live`, `gateway.scored.live` | — (WebSocket) |

### Routing topology (topic exchange `feed.events`)

```
article.ingested → event.detected → prediction.made → price.requested
                                                              ↓
                          prediction.scored ← price.observed
```

Verification is the sole producer of `price.requested` and `prediction.scored`. `prediction.made`
and `prediction.scored` also fan out to dedicated Gateway live queues.

### Databases

Two engines:
- **Postgres 16 with pgvector**: ONE database (`feed`) with six service-owned schemas
  (`ingestion`, `cleansing`, `prediction`, `market_data`, `verification`, `credibility`).
- **Neo4j 5**: one graph database for the causal knowledge graph.

### Message bus

RabbitMQ 3.13 with the durable topic exchange `feed.events`. Each consumer has its own durable work
queue bound by routing key; every durable work queue has a dedicated `<queue>.dlq` via `feed.dlx`.
Gateway live queues are non-durable because REST supplies catch-up state.

## Overall Acceptance Criteria

1. `docker compose up` starts all containers (Postgres, Neo4j, RabbitMQ) and all pass health checks within 60 seconds.
2. The single `feed` database exists with all six service-owned schemas and the `pgvector` extension enabled.
3. Neo4j seeds the canonical `GOLD`/`BRENT_OIL` assets, taxonomy causal factors, and at least 15 `CAUSES` edges (magnitude weight in [0,1] + separate direction, prior `alpha=1.0/beta=1.0`).
4. The `feed.events` topic exchange, all canonical consumer queues, `feed.dlx`, and per-queue DLQs are declared durable.
5. `src/shared/` package installs via `pip install -e src/shared/` with no errors.
6. All Pydantic schemas can be serialized to JSON and deserialized back without data loss.
7. The async RabbitMQ client can publish and consume a message in a pytest-asyncio test.
8. The LLM gateway returns a validated structured response from the configured provider/model when the matching API key is present, and a replayed request makes zero provider calls (cache hit).
9. structlog emits JSON log lines including `correlation_id` in every entry.
10. All `src/shared/` code passes `ruff check` and `mypy --strict`.
