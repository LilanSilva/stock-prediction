# infra — Infrastructure code

All infrastructure code: the local Docker Compose stack, the database and broker initialisation, the
message topology, the graph seed data, and the deployed asset registry.

Everything here must be **idempotent** — every init and seed script is re-run on each stack start, so
a second run must change nothing.

## Files and folders

| Path | Contains |
|---|---|
| [docker-compose.yml](docker-compose.yml) | The whole local stack: Postgres, Neo4j, a one-shot Neo4j seed container, RabbitMQ, and the seven services |
| [postgres/01-init-database.sql](postgres/01-init-database.sql) | Creates the six service-owned schemas and the `vector` extension. Tables are created by each service, not here |
| [postgres-image/Dockerfile](postgres-image/Dockerfile) | Builds Postgres 16 + `pgvector` from the org-approved official base |
| [rabbitmq/definitions.json](rabbitmq/definitions.json) | The exchange, queues, bindings, and DLQs — the full message topology |
| [rabbitmq/rabbitmq.conf](rabbitmq/rabbitmq.conf) | Broker configuration |
| [rabbitmq/render-definitions.sh](rabbitmq/render-definitions.sh) | Injects the broker user at startup and computes its password hash at runtime |
| [neo4j/init/](neo4j/init/) | Seven Cypher scripts: constraints, then the causal graph seed data |
| [assets/assets.json](assets/assets.json) | The deployed asset registry, mounted read-only at `/config/assets.json` |

## Bring up the stack

```bash
cp infra/.env.example infra/.env   # then edit infra/.env with local values
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build
```

Validate changes without starting anything:

```bash
docker compose --env-file infra/.env.example -f infra/docker-compose.yml config
python -m json.tool infra/rabbitmq/definitions.json
```

## Engines

| Engine | Version | Holds |
|---|---|---|
| PostgreSQL + pgvector | 16 | One database `feed`, six service-owned schemas; pgvector holds article embeddings |
| Neo4j | 5 | The causal knowledge graph and its learned edge weights — nothing else |
| RabbitMQ | 3.13 | The durable topic exchange `feed.events` and every consumer queue |

Two environment-specific notes apply to this repository:

- **Postgres/pgvector image:** the community `pgvector/pgvector` image may be blocked by org registry
  policy, so Postgres is built from the org-approved official `postgres:16` base plus the `pgvector`
  package. The compose `feed-postgres` service therefore uses `build:` rather than a community
  `image:`.
- **RabbitMQ user:** when a definitions file is loaded, RabbitMQ will not seed
  `RABBITMQ_DEFAULT_USER`, so the broker user is injected at startup from environment variables by
  `render-definitions.sh`. The tracked `definitions.json` stays topology-only with `"users": []`.

## Neo4j seed order

Neo4j does not auto-run Cypher on start, so a one-shot `feed-neo4j-seed` container applies these in
filename order once Neo4j is healthy. All use `MERGE`, so re-runs are safe.

| File | Content |
|---|---|
| `01-constraints-indexes.cypher` | Uniqueness constraints and indexes |
| `02-seed-assets.cypher` | Asset and group nodes — **generated**, see below |
| `03-seed-causal-factors.cypher` | One node per event taxonomy type |
| `04-seed-causal-edges.cypher` | Expert-assigned unconditional edges |
| `05-seed-conditioned-edges.cypher` | Condition-qualified edges |
| `06-seed-group-edges.cypher` | Industry-level priors, inherited by members |
| `07-seed-new-event-type-edges.cypher` | Edges for later taxonomy additions |

Every seeded edge starts at `alpha = 1.0`, `beta = 1.0`.

**`02-seed-assets.cypher` is generated, not hand-edited.** Regenerate it after any registry change:

```bash
python scripts/generate-asset-seed.py
```

## Secrets and environment variables

Every secret lives in **one** place: `infra/.env` (git-ignored). Nothing secret is ever written to a
tracked file. This applies to everyone — humans and coding agents alike.

- Document each variable — names and placeholder values only — in [.env.example](.env.example); never
  put a real value there.
- Services and tests read credentials from environment variables (`DATABASE_URL`, `NEO4J_URI`,
  `RABBITMQ_URL`, `RABBITMQ_DEFAULT_USER`/`PASS`, LLM keys, …).
- **Never hardcode a password, token, or even a password hash in a tracked file.** When a tool needs a
  derived credential at rest — like RabbitMQ's `password_hash` — compute it at runtime from the env
  var, as `render-definitions.sh` does.
- `infra/.env` and `.env` are already excluded in [.gitignore](../.gitignore); keep them untracked.
- Never log a secret.

## Other rules

- `definitions.json` must match [SRS-01 §8.4](../requirements/SRS-01-shared-foundation.md#84-rabbitmq-topology)
  exactly. Every durable work queue has a dedicated DLQ through `feed.dlx`.
- Every init and seed script must be idempotent — they re-run on every stack start.
- Neo4j seed edges use separate `direction` and magnitude `weight` fields; `weight` is in `[0,1]` and
  Beta-Bernoulli priors start at `alpha=1.0`, `beta=1.0`.
- Editing `assets/assets.json` plus a service restart is the whole workflow for adding an asset — the
  registry loads once at process start by design. Rules in
  [REF-02](../requirements/REF-02-asset-registry.md).

## Validating a change

At minimum:

```bash
docker compose --env-file infra/.env.example -f infra/docker-compose.yml config
python -m json.tool infra/rabbitmq/definitions.json
```

When Docker is available, also start the stack, wait for healthy services, verify the Postgres schemas
and pgvector, verify the Neo4j constraints and seeds and at least 15 `CAUSES` edges, inspect the
RabbitMQ exchanges/queues/bindings, then tear down the test volumes.

Specification: [SRS-01](../requirements/SRS-01-shared-foundation.md) covers the shared foundation and
this local infrastructure. System-wide topology is in
[SyRS §7.1](../requirements/SyRS-system.md#71-routing-topology) and
[§9](../requirements/SyRS-system.md#9-data-design).
