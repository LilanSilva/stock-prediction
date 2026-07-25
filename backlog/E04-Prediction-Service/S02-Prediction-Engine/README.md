# S02 - Prediction Engine

## Overview

This story implements the core runtime logic of the Prediction Service. It subscribes to prediction events, aggregates distinct events into per-asset context windows, queries Neo4j for firing causal edges, produces graph-only predictions for M1, stores the result in Postgres, and publishes `PredictionMade`.

This story depends on S01 (knowledge graph must exist and be seeded before predictions can be made).

## Tasks

| Task | Name | Description |
|---|---|---|
| T01 | Event-to-graph matcher | Consumes `EventDetected`, queries Neo4j, groups firing edges by asset |
| T02 | Graph-only decision policy | Resolves signed graph forces into direction, magnitude, confidence, and rationale without LLM calls |
| T03 | Prediction storage & publishing | Persists prediction to Postgres and publishes `PredictionMade` to RabbitMQ |

## Dependencies

- S01 (T01 and T02) must be complete: Neo4j running with schema and seed data
- `src/shared/` library must provide:
  - Pydantic schemas: `EventDetected`, `PredictionMade`
  - `RabbitMQClient` wrapper with `consume` and `publish` methods
  - Postgres connection helper
- Postgres `predictions` table must exist (created as part of this story T03)
- Environment variables: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `RABBITMQ_URL`, `POSTGRES_DSN`

## End-to-End Test

1. Start all infra: `docker compose up neo4j rabbitmq postgres`
2. Run seed: `python infra/neo4j/seed_knowledge_graph.py`
3. Start the Prediction Service: `docker compose up prediction`
4. Publish a test `EventDetected` message to the `events` queue:
   ```json
   {
     "event_id": "test-001",
     "canonical_summary": "Military attack on oil infrastructure",
     "event_type": "military_conflict",
     "actor": "Unknown",
     "action": "attack",
     "object": "oil refinery",
     "entities": ["oil", "war", "attack"],
     "affected_assets": ["BZ=F", "GC=F"],
     "first_seen": "2025-01-01T10:00:00Z",
     "source_count": 3,
     "sources": [],
     "fact_conflicts": [],
     "correlation_id": "corr-001"
   }
   ```
5. Check Postgres: `SELECT * FROM predictions WHERE correlation_id = 'corr-001'` - expect rows for each asset.
6. Check RabbitMQ `predictions` queue: expect `PredictionMade` messages.
7. Confirm no `PriceRequested` message is published by Prediction; Verification owns that later step.
