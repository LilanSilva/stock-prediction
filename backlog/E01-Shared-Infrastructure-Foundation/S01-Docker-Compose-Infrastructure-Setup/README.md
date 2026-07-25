# S01: Docker Compose & Infrastructure Setup

> Contract-freeze status: governed by the [backlog override matrix](../../contract-freeze-overrides.md).
> The delivered infra uses ONE Postgres database with service-owned schemas and the `feed.events`
> topic exchange — not the per-service databases/queues named in older drafts.

## Overview

This story brings up the full local development environment. It contains four tasks that together produce a `docker compose up` command that starts Postgres (with pgvector), Neo4j, and RabbitMQ with all schemas, seeds, and queue declarations in place.

No application service code is written here. This story is purely infrastructure.

## Secrets

Every secret lives only in `infra/.env` (git-ignored); `infra/.env.example` documents the variable
names with placeholders. Nothing secret — not even a derived value such as RabbitMQ's
`password_hash` — is committed. The RabbitMQ broker user is injected at container startup from the
env vars by `infra/rabbitmq/render-definitions.sh`, and Postgres builds pgvector from the official
`postgres:16` base (`infra/postgres-image/Dockerfile`) because the community image is blocked by org
policy. All coding agents must follow this same env-only pattern.

## Tasks

| Task | Description |
|---|---|
| T01 - Docker Compose base configuration | `infra/docker-compose.yml` with all containers, health checks, volumes, and a shared network |
| T02 - Postgres initialization scripts | SQL scripts in `infra/postgres/` that create the single `feed` database's six service-owned schemas and enable the pgvector extension |
| T03 - Neo4j initialization & schema | Cypher scripts in `infra/neo4j/` that set up constraints, indexes, and seed the causal knowledge graph |
| T04 - RabbitMQ setup & queue declarations | RabbitMQ definitions JSON in `infra/rabbitmq/` that declares the `feed.events` topic exchange, canonical consumer queues, `feed.dlx`, and per-queue DLQs with durability |

## Dependencies

None. This is the first story in the project. It requires only Docker Desktop installed on the developer machine.

## How to Test End-to-End

1. Copy the env template and fill in local values: `cp infra/.env.example infra/.env`
2. From the repo root, run: `docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build`
3. Wait for health checks: `docker compose --env-file infra/.env -f infra/docker-compose.yml ps` — all services should show `healthy`.
4. Verify pgvector: `docker exec -it feed-postgres psql -U feed_user -d feed -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"` — should return one row.
5. Verify schemas: `docker exec -it feed-postgres psql -U feed_user -d feed -c "\dn"` — should list `ingestion`, `cleansing`, `prediction`, `market_data`, `verification`, `credibility`.
6. Verify Neo4j seed data: open `http://localhost:7474`, run `MATCH ()-[r:CAUSES]->() RETURN count(r)` — should return at least 15.
7. Verify RabbitMQ: open `http://localhost:15672` (log in with the `.env` credentials), Queues tab — the six consumer queues plus their `.dlq` queues are listed as durable, and the `feed.events` topic exchange exists.
8. Tear down: `docker compose --env-file infra/.env -f infra/docker-compose.yml down -v`
