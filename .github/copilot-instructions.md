# Feed Analyzer Development Guidance

## Project purpose and current scope

Feed Analyzer is a local proof of concept for predicting one-trading-day market direction from
structured news events and a causal graph. The initial walking skeleton supports only the canonical
assets `GOLD` and `BRENT_OIL`. It does not execute trades or provide personalized financial advice.

POC-6 recorded `STOP` for prediction-time KG-plus-LLM arbitration. M1 prediction is graph-only.
Do not add prediction-time LLM calls unless a new controlled hypothesis is explicitly approved.

## Source-of-truth order

Resolve conflicts in this order:

1. Executable Pydantic models in `src/shared/shared/schemas/`
2. `docs/contracts/message-contracts.md`
3. `docs/requirements/agreed-system-requirements.md`
4. Accepted decisions in `docs/decisions/README.md`
5. Current functional documents and architecture diagrams
6. `backlog/contract-freeze-overrides.md`
7. Epic, story, and task detail under `backlog/`

Read the repository `README.md` and `backlog/contract-freeze-overrides.md` before implementing a
backlog item. Never implement a legacy task detail that conflicts with a higher-authority source.

## Architecture invariants

- Use one PostgreSQL database named `feed` with service-owned schemas: `ingestion`, `cleansing`,
  `prediction`, `market_data`, `verification`, and `credibility`.
- Neo4j stores the causal graph only.
- Publish persistent domain events to the durable `feed.events` topic exchange by canonical routing
  key. Every consumer owns an independent queue; observers never consume another service's work
  queue.
- Every durable work queue has a dedicated DLQ through `feed.dlx`.
- Use canonical asset IDs at service boundaries. Provider symbols such as `GC=F` and `BZ=F` belong
  only inside market-data adapters.
- Verification alone produces `PriceRequested`, containing both baseline and settlement sessions.
  Market Data returns both immutable closes in one `PriceObserved`.
- State-changing persistence plus publication requires an outbox or equivalent reconciliation.
  Consumers must be idempotent.

## Canonical messages

The six domain messages are `ArticleIngested`, `EventDetected`, `PredictionMade`,
`PriceRequested`, `PriceObserved`, and `PredictionScored`.

All envelopes contain `message_id`, `correlation_id`, `causation_id`, `occurred_at`, and
`schema_version`. Business timestamps are timezone-aware UTC, enum values use uppercase snake case,
and the initial schema version is `1.0`.

When a message contract changes, update the executable model, contract documentation, routing map,
and producer/consumer contract tests together. Adding an optional field with a default may be
compatible within major version 1; removing, renaming, changing a type, or changing enum meaning is
a major-version change.

## LLM policy

- Prefer local deterministic processing.
- M1 may use the shared LLM gateway only for ambiguous cleansing extraction, merge, or factual
  conflict resolution.
- Keep provider and model selection in environment-backed settings.
- Enforce compact input/output budgets, bounded retries, structured-output validation, and cache
  identity based on provider, model, task, prompt version, schema, and context/input.
- Capture provider-reported token usage and latency without making an additional call.
- Do not log secrets, complete prompts, or full article bodies.

## Python conventions

- Python version: 3.12 or newer.
- Shared library project root: `src/shared/`; import package: `shared`.
- Use Pydantic v2 models and `pydantic-settings` for environment configuration.
- Keep public functions and classes fully typed. The project uses `mypy --strict`.
- Format and lint with Ruff using the settings in `src/shared/pyproject.toml`.
- Prefer immutable message/value models and explicit typed exceptions.
- Add comments only for non-obvious design constraints or reliability behavior.
- Preserve async cancellation and use graceful close/shutdown paths for external clients.

## Infrastructure conventions

- Local infrastructure lives under `infra/`.
- Compose must start Postgres with pgvector, Neo4j, a one-shot Neo4j seed container, and RabbitMQ.
- Initialization and seed scripts must be idempotent.
- Never hardcode real credentials, and never commit a password or a derived credential such as a
  password **hash**. Store every secret only in `infra/.env` (git-ignored); document variable names
  with placeholder values in `infra/.env.example`. Services and tests read credentials from
  environment variables. When a tool needs a derived credential at rest, compute it at runtime from
  the env var instead of committing it.
- The Postgres service builds a local pgvector image from the org-approved official `postgres:16`
  base (`infra/postgres-image/Dockerfile`); the community `pgvector/pgvector` image is blocked by
  org registry policy.
- RabbitMQ will not seed `RABBITMQ_DEFAULT_USER` when a definitions file is loaded, so the broker
  user is injected at startup from env vars by `infra/rabbitmq/render-definitions.sh` (hash computed
  at runtime). The tracked `infra/rabbitmq/definitions.json` stays topology-only with `"users": []`.
- RabbitMQ definitions must match `docs/contracts/message-contracts.md` exactly.
- Neo4j seed edges use separate `direction` and magnitude `weight` fields; `weight` is in `[0,1]`
  and Beta-Bernoulli priors start at `alpha=1.0`, `beta=1.0`.

## Testing and review

For shared-library changes, run from `src/shared/`:

```bash
python -m pytest
python -m ruff check .
python -m mypy shared tests
```

Use the fastest targeted test first, then the full shared suite. Unit tests must not require live
infrastructure or provider credentials. Mark live RabbitMQ and real-provider checks as integration
tests and skip them when required configuration is absent.

For infrastructure changes, at minimum validate:

```bash
docker compose --env-file infra/.env.example -f infra/docker-compose.yml config
python -m json.tool infra/rabbitmq/definitions.json
```

When Docker is available, also start the stack, wait for healthy services, verify Postgres schemas
and pgvector, verify Neo4j constraints/seeds and at least 15 `CAUSES` edges, inspect RabbitMQ
exchanges/queues/bindings, and tear down the test volumes.

Review changes for contract alignment, canonical identifiers, idempotency, failure handling,
correlation propagation, secret safety, and backward compatibility. Do not broaden scope into
deferred epics while completing an earlier epic.
