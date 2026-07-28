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
2. The canonical graph is seeded automatically by the one-shot `feed-neo4j-seed` container (`infra/neo4j/init/`).
3. Start the Prediction Service: `docker compose -f infra/docker-compose.yml up -d feed-prediction`
4. A canonical `EventDetected` (routing key `event.detected`, consumed from `prediction.events`) looks like:
   ```json
   {
     "correlation_id": "4cb26473-b48a-46ff-9359-3948f15b9e54",
     "occurred_at": "2026-01-01T10:05:00Z",
     "event_id": "dea8a3fd-6dc6-47c4-b2f1-701756932d09",
     "cluster_id": "3b6a5aa7-c7ef-4591-aab1-e3abd3494699",
     "canonical_summary": "Military attack on oil infrastructure",
     "event_type": "MILITARY_CONFLICT",
     "actor": "Unknown", "action": "attack", "object": "oil refinery",
     "entities": ["oil"],
     "affected_asset_ids": ["BRENT_OIL", "GOLD"],
     "first_seen_at": "2026-01-01T10:00:00Z",
     "last_seen_at": "2026-01-01T10:04:00Z",
     "sources": [],
     "fact_conflicts": [],
     "extraction_method": "LOCAL",
     "llm_metadata": null
   }
   ```
   Note: canonical `AssetId` values (`BRENT_OIL`, `GOLD`) — never provider symbols like `BZ=F`/`GC=F`.
5. Check Postgres: `SELECT * FROM prediction.predictions ORDER BY created_at DESC` — expect one row per ready asset/context version with `decision_method = GRAPH_ONLY`.
6. Check RabbitMQ `predictions` queue: expect `PredictionMade` messages.
7. Confirm no `PriceRequested` message is published by Prediction; Verification owns that later step.
