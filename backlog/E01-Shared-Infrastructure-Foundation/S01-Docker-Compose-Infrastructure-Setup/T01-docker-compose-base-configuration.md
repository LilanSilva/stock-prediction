# T01: Docker Compose Base Configuration

## Context

This task creates `infra/docker-compose.yml`, the single file that starts the entire local development environment. Every other service in the system depends on the containers defined here. This is the first task to complete in the project — nothing else can run without it.

## Background

The system uses three infrastructure components:
- **Postgres 16** with the `pgvector` extension for all relational and vector-embedding data
- **Neo4j 5** for the causal knowledge graph (signed weighted edges between assets and events)
- **RabbitMQ 3.13** as the message bus connecting all six microservices

All three must be on the same Docker network so that service containers (added in later epics) can reach them by hostname. Volumes must be named so data survives `docker compose restart`.

Health checks are critical: application services (added in later epics) will use `depends_on: condition: service_healthy` to avoid race conditions on startup.

## Inputs

- No external inputs. This task starts from scratch.
- Assumes Docker Desktop is installed and the Docker daemon is running.
- Initialization scripts from T02 (Postgres SQL), T03 (Neo4j Cypher), and T04 (RabbitMQ definitions JSON) will be mounted into the respective containers. This task must define the mount paths so T02/T03/T04 know where to place their files.

## Outputs

- `infra/docker-compose.yml` — the main Compose file
- `infra/.env.example` — example environment variable file documenting all required variables

## Technical Requirements

### File locations

```
infra/
  docker-compose.yml
  .env.example
  postgres/        # init scripts go here (created in T02)
  neo4j/           # init scripts go here (created in T03)
  rabbitmq/        # definitions go here (created in T04)
```

### Postgres service (`feed-postgres`)

- Image: built locally from `infra/postgres-image/Dockerfile` (official `postgres:16` base +
  `pgvector`), tagged `feed-analyzer/postgres-pgvector:16`. The community `pgvector/pgvector` image
  is blocked by org registry policy, so the compose service uses `build:` rather than a community
  `image:`.
- Container name: `feed-postgres`
- Port mapping: `5432:5432`
- Environment variables (read from `.env` file):
  - `POSTGRES_USER=feed_user`
  - `POSTGRES_PASSWORD` (secret, no default in compose file)
  - `POSTGRES_DB=feed_admin` (default admin DB; individual databases created by init scripts)
- Volume mounts:
  - `./postgres/:/docker-entrypoint-initdb.d/` — Postgres runs all `.sql` files here on first start
  - `feed-postgres-data:/var/lib/postgresql/data` — persistent volume
- Health check:
  ```yaml
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U feed_user -d feed_admin"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s
  ```
- Networks: `feed-net`

### Neo4j service (`feed-neo4j`)

- Image: `neo4j:5.20-community`
- Container name: `feed-neo4j`
- Port mappings: `7474:7474` (HTTP browser), `7687:7687` (Bolt protocol)
- Environment variables:
  - `NEO4J_AUTH=neo4j/feedpassword` (or from `.env`)
  - `NEO4J_PLUGINS=[\"apoc\"]` — enable APOC plugin
  - `NEO4J_dbms_security_procedures_unrestricted=apoc.*`
  - `NEO4J_dbms_security_procedures_allowlist=apoc.*`
  - `NEO4J_server_memory_heap_initial__size=512m`
  - `NEO4J_server_memory_heap_max__size=1G`
- Volume mounts:
  - `./neo4j/init/:/var/lib/neo4j/import/` — Cypher init files (T03 will place files here)
  - `feed-neo4j-data:/data` — persistent volume
  - `feed-neo4j-logs:/logs`
- Health check:
  ```yaml
  healthcheck:
    test: ["CMD-SHELL", "wget -O /dev/null -q http://localhost:7474 || exit 1"]
    interval: 15s
    timeout: 10s
    retries: 10
    start_period: 60s
  ```
- Networks: `feed-net`

**Note on Neo4j seeding**: Neo4j does not auto-run Cypher files on startup like Postgres does with SQL files. T03 will handle seeding via a separate `neo4j-seed` init container defined in this same Compose file (see below).

### Neo4j seed init container (`feed-neo4j-seed`)

- Image: `neo4j:5.20-community`
- Runs once: `restart: no`
- Depends on `feed-neo4j` being healthy
- Command runs the seed Cypher files using `cypher-shell`:
  ```yaml
  command: >
    bash -c "
      for f in /seed/*.cypher; do
        echo Running $$f;
        cypher-shell -a bolt://feed-neo4j:7687 -u neo4j -p feedpassword --file $$f;
      done
    "
  ```
- Volume mounts: `./neo4j/init/:/seed/` (same files T03 creates)
- Networks: `feed-net`

### RabbitMQ service (`feed-rabbitmq`)

- Image: `rabbitmq:3.13-management`
- Container name: `feed-rabbitmq`
- Port mappings: `5672:5672` (AMQP), `15672:15672` (management UI)
- Environment variables:
  - `RABBITMQ_DEFAULT_USER=feed_user`
  - `RABBITMQ_DEFAULT_PASS` (from `.env`)
- Definitions loading is configured in `rabbitmq.conf` (`management.load_definitions`), not via
  `RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS`. The broker user is injected at startup from the env vars by
  `render-definitions.sh` (see T04), so no `password_hash` is committed.
- Volume mounts:
  - `./rabbitmq/definitions.json:/etc/rabbitmq/definitions.json:ro` (topology template; T04 creates it)
  - `./rabbitmq/render-definitions.sh:/usr/local/bin/render-definitions.sh:ro` (T04 creates it)
  - `./rabbitmq/rabbitmq.conf:/etc/rabbitmq/rabbitmq.conf:ro`
  - `feed-rabbitmq-data:/var/lib/rabbitmq`
- Health check:
  ```yaml
  healthcheck:
    test: ["CMD", "rabbitmq-diagnostics", "ping"]
    interval: 10s
    timeout: 5s
    retries: 10
    start_period: 30s
  ```
- Networks: `feed-net`

### Named volumes

```yaml
volumes:
  feed-postgres-data:
  feed-neo4j-data:
  feed-neo4j-logs:
  feed-rabbitmq-data:
```

### Network

```yaml
networks:
  feed-net:
    driver: bridge
```

### `.env.example` content

Document all variables:
```
POSTGRES_USER=feed_user
POSTGRES_PASSWORD=changeme
NEO4J_AUTH=neo4j/feedpassword
RABBITMQ_DEFAULT_USER=feed_user
RABBITMQ_DEFAULT_PASS=changeme
LLM_PROVIDER=openai
LLM_MODEL=<provider-model-id>
LLM_BASE_URL=
LLM_API_KEY=
```

## Acceptance Criteria

1. `docker compose -f infra/docker-compose.yml up -d` completes without errors.
2. `docker compose -f infra/docker-compose.yml ps` shows all containers with status `healthy` within 90 seconds of starting.
3. Postgres is reachable at `localhost:5432` with credentials from `.env`.
4. Neo4j browser is reachable at `http://localhost:7474`.
5. Neo4j Bolt endpoint responds at `bolt://localhost:7687`.
6. RabbitMQ management UI is reachable at `http://localhost:15672`.
7. `docker compose -f infra/docker-compose.yml down -v` cleanly removes all containers and volumes.
8. Re-running `docker compose up` after `down -v` starts fresh with no stale state.
9. The `feed-neo4j-seed` container exits with code 0 after `feed-neo4j` is healthy.
10. All services share the `feed-net` bridge network (verify: `docker network inspect feed-net`).

## Implementation Notes

- Use `env_file: .env` in the Compose file so secrets are not hardcoded. Copy `.env.example` to `.env` and fill in values before running.
- The Postgres image is built from the official `postgres:16` base with the `pgvector` package (see
  `infra/postgres-image/Dockerfile`) because the community `pgvector/pgvector` image is blocked by
  org registry policy. The extension must still be enabled per-database via
  `CREATE EXTENSION IF NOT EXISTS vector;` in the SQL init scripts (T02).
- Neo4j 5 community edition does not support clustering. That is fine for local development.
- The `NEO4J_PLUGINS=[\"apoc\"]` env var uses the Docker image's built-in plugin download mechanism — it requires internet access on first start.
- RabbitMQ's `load_definitions` approach (loading `definitions.json` at boot) is the cleanest way to declare queues declaratively. T04 produces this file.
- Place a minimal `infra/rabbitmq/rabbitmq.conf` with at minimum `management.load_definitions = /etc/rabbitmq/definitions.json` to avoid warnings.
- Do not pin Compose file version (the `version:` key is deprecated in Docker Compose v2+).

## Definition of Done

- [x] `infra/docker-compose.yml` exists and is valid YAML (`docker compose config` exits 0)
- [x] `infra/.env.example` documents all required environment variables
- [x] All four containers start and reach `healthy` state in CI (or local Docker Desktop)
- [ ] `docker compose down -v` followed by `docker compose up -d` produces identical state
- [x] No secrets are hardcoded in `docker-compose.yml` (all via `.env`)
- [x] Mount paths for T02, T03, T04 init files are defined and documented in this file
