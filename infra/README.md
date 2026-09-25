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
| [assets/avanza-listings.json](assets/avanza-listings.json) | Disabled, validated companion listing mappings; identity/version rules in [REF-02](../requirements/REF-02-asset-registry.md#avanza-companion-mappings) |

## Bring up the stack

The optional `snapshots` profile adds a browser worker. Deployment, CA setup, resource limits,
rollout/rollback and recovery are owned by
[SRS-05](../requirements/SRS-05-market-data.md#snapshot-deployment-and-recovery).
Normal stack startup does not enable it.

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
| `04-seed-causal-edges.cypher` | **Retired** — targeted `GOLD`/`BRENT_OIL`, which `02` never creates |
| `05-seed-conditioned-edges.cypher` | Condition-qualified edges. **Owns every conditioned edge** |
| `06-seed-group-edges.cypher` | Industry-level priors, inherited by members. **Owns every unconditional edge** |
| `07-seed-new-event-type-edges.cypher` | Edges for later taxonomy additions |
| `08-seed-correlation-edges.cypher` | Cross-asset `CORRELATES_WITH` edges (propagation) |
| `09-verify-seed.cypher` | Structural assertions; **aborts the seed** on failure |

Every seeded edge starts at `alpha = 1.0`, `beta = 1.0`.

### A `(factor, target)` pair belongs to exactly one file

`MERGE (cf)-[r:CAUSES]->(g)` with no properties matches **any** existing `CAUSES` relationship between
the two nodes, including a conditioned one. So an unconditional statement running after a conditioned one
does not create a second edge — it silently rebinds the conditioned edge and overwrites its `weight` and
`confidence`, leaving `condition` in place. Verified on 2026-08-14: re-seeding an unconditional
`MILITARY_CONFLICT → PRECIOUS_METALS` edge rewrote the `TRANSPORT_AFFECTED` variant's 0.55/0.65 to
0.50/0.60.

`09-verify-seed.cypher` cannot catch this — the corrupted edge is structurally valid — so it is enforced
by the file split above and by review.

A target should carry a conditioned edge only where the condition changes the **outcome**, meaning only
where one condition implies no edge at all. Condition tags are unioned across the events in a context, so
`SAFE_HAVEN_ONLY` and `TRANSPORT_AFFECTED` can both be active; a target holding one edge per condition
would fire both and count the factor twice.

### `09-verify-seed.cypher`

Cypher's `MATCH ... MERGE` is a silent no-op when the `MATCH` binds nothing, and `cypher-shell` exits 0
regardless. `04` targeted `(:Asset {id: 'GOLD'})` and `BRENT_OIL`, which the registry migration removed,
so a whole file of expert priors was discarded at seed time with no error for as long as it existed.

The verifier runs last (it sorts last in the `for f in /seed/*.cypher` glob) and asserts:

1. every `CausalFactor` has at least one outgoing `CAUSES` edge;
2. no `CAUSES`/`CORRELATES_WITH` edge is missing `direction`, `weight`, `alpha` or `beta`;
3. no `(factor, target)` pair carries both a conditioned and an unconditional edge;
4. the retired `GOLD`/`BRENT_OIL` ids have not reappeared;
5. every asset belongs to exactly one group;
6. every `CORRELATES_WITH` edge carries a condition.

Each check uses `apoc.util.validate`, which raises and makes `cypher-shell` exit non-zero, so `set -e` in
the seed container's entrypoint fails the whole seed. To confirm it still bites:

```bash
# Inject the original defect, then re-run the verifier — it must exit 1.
docker exec feed-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "CREATE (:Asset {id:'GOLD'});"
docker exec -i feed-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" < neo4j/init/09-verify-seed.cypher
echo "exit=$?"   # 1, with: seed check 4: retired commodity nodes are present again: [Asset GOLD]
docker exec feed-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (n:Asset {id:'GOLD'}) DETACH DELETE n;"
```

### Re-seeding an existing volume

`MERGE` is idempotent, but it **cannot undo a deletion**: `05` deletes the unconditional edges its
conditioned edges supersede, and re-running the files will not restore anything removed by an earlier
version of them. Before re-seeding a graph whose weights Credibility has been learning, dump them:

```bash
docker exec feed-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" --format plain \
  "MATCH (cf:CausalFactor)-[r:CAUSES]->(t)
   RETURN cf.id, labels(t)[0], t.id, r.condition, r.direction, r.weight, r.alpha, r.beta;" \
  > graph-causes-backup.csv
```

To rebuild from scratch instead, remove the volume — this discards every learned weight:

```bash
docker compose down
docker volume rm feed-neo4j-data feed-neo4j-logs
docker compose up -d
```

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

Intraday rollout configuration and the empirical coverage gate are owned by
[SRS-06](../requirements/SRS-06-verification.md#13-assumptions-and-limitations). Both services are
disabled by default. Enabling either performs an additive queue migration against the existing
broker; this does not require restarting RabbitMQ or reseeding Neo4j. The exact queue contract is in
[SRS-01](../requirements/SRS-01-shared-foundation.md#84-rabbitmq-topology).

At minimum:

```bash
docker compose --env-file infra/.env.example -f infra/docker-compose.yml config
python -m json.tool infra/rabbitmq/definitions.json
```

When Docker is available, also start the stack, wait for healthy services, verify the Postgres schemas
and pgvector, confirm the `feed-neo4j-seed` container exited 0 (which now includes
`09-verify-seed.cypher`'s assertions), inspect the RabbitMQ exchanges/queues/bindings, then tear down the
test volumes.

Specification: [SRS-01](../requirements/SRS-01-shared-foundation.md) covers the shared foundation and
this local infrastructure. System-wide topology is in
[SyRS §7.1](../requirements/SyRS-system.md#71-routing-topology) and
[§9](../requirements/SyRS-system.md#9-data-design).
