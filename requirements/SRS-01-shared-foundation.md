# SRS-01: Shared Foundation and Local Infrastructure

## 1. Document control

| | |
|---|---|
| Document ID | `SRS-01` |
| Component | Shared Python library (`shared`) and local infrastructure |
| Requirement ID prefix | `SHR` |
| Status | `Implemented` |
| Version | `1.1.0` |
| Source code | [src/shared/shared/](../src/shared/shared/), [infra/](../infra/) |
| Tests | [src/shared/tests/](../src/shared/tests/) |
| Last verified against code | `2026-08-05` |

## 2. Purpose and scope

### 2.1 What this component does

The shared foundation is everything the six services have in common. It is not a running service —
it is one installable Python package plus the Docker infrastructure the services connect to.

It exists so that a rule is written once. The message schemas, the session calendar, the asset
registry, the graph queries, and the logging format all live here, because if two services disagreed
about any of them the pipeline would silently corrupt data.

Two parts:

1. **The `shared` library** — imported by every service.
2. **The local infrastructure** — PostgreSQL, Neo4j, and RabbitMQ under Docker Compose, plus the
   scripts that initialise and seed them.

### 2.2 In scope

- Pydantic message models for all six messages, plus every shared enum and nested type.
- The `AssetId` type and the JSON asset registry loader.
- The async RabbitMQ client wrapper.
- The provider-configurable LLM gateway with caching and validation.
- The Neo4j graph client and its Cypher queries.
- The market-session calendar.
- Structured JSON logging and correlation-ID propagation.
- Docker Compose definitions, PostgreSQL init SQL, Neo4j seed Cypher, RabbitMQ topology definitions.

### 2.3 Explicitly out of scope

| Out of scope | Where it belongs |
|---|---|
| Any business decision (clustering, prediction, scoring, learning) | The owning service |
| Application table DDL | Each service's own `db.py` |
| Choosing an LLM provider or model | Deployment configuration |
| Provider symbol translation | Market Data adapters |

## 3. Definitions

| Term | Meaning |
|---|---|
| Envelope | The five fields every message carries: `message_id`, `correlation_id`, `causation_id`, `occurred_at`, `schema_version` |
| `FeedMessage` | The frozen Pydantic base class all six messages inherit |
| `AssetId` | A `str` subclass validated against the loaded registry |
| Registry version | A label identifying one snapshot of the asset registry, e.g. `multi-market-v2` |
| Firing edge | A `CAUSES` edge returned by the graph query because its condition is satisfied |
| Reliability | An edge's learned `alpha / (alpha + beta)` |
| Session | One trading day on a specific market's calendar |
| Structured output | An LLM response validated against a caller-supplied JSON schema |

## 4. System context

### 4.1 Position

```text
       +--------------------------------------------------+
       |  shared library (imported by all six services)   |
       |  schemas | messaging | llm | graph | calendar |   |
       |  reference | logging                             |
       +--------------------------------------------------+
                |               |               |
                v               v               v
         PostgreSQL 16      Neo4j 5        RabbitMQ 3.13
         + pgvector
```

### 4.2 Modules

| Module | Responsibility | Path |
|---|---|---|
| `shared.schemas` | Message models, enums, `AssetId` | [schemas/](../src/shared/shared/schemas/) |
| `shared.messaging` | Async publish and consume | [messaging/](../src/shared/shared/messaging/) |
| `shared.llm` | Provider-configurable gateway, cache, validation | [llm/](../src/shared/shared/llm/) |
| `shared.graph` | Neo4j client, Cypher, models | [graph/](../src/shared/shared/graph/) |
| `shared.calendar` | Session resolution and completion | [calendar/](../src/shared/shared/calendar/) |
| `shared.reference` | Asset registry loading and accessors | [reference/](../src/shared/shared/reference/) |
| `shared.logging` | JSON logging, correlation-ID middleware | [logging/](../src/shared/shared/logging/) |

### 4.3 Dependencies

| Dependency | Purpose | Failure impact |
|---|---|---|
| PostgreSQL 16 + pgvector | All service state; pgvector holds embeddings | No service can start |
| Neo4j 5 | Causal graph | Prediction and Credibility cannot work |
| RabbitMQ 3.13 | Message transport | The pipeline stops; outboxes retain work |
| An LLM provider | Ambiguous cleansing only | Ambiguous clusters wait; nothing is fabricated |

## 5. Functional requirements

### 5.1 Message schemas

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-1` | The library **shall** define Pydantic v2 models for `ArticleIngested`, `EventDetected`, `PredictionMade`, `PriceRequested`, `PriceObserved`, and `PredictionScored`. | Must | Implemented |
| `SHR-2` | Every message model **shall** inherit an envelope providing `message_id`, `correlation_id`, `causation_id`, `occurred_at`, and `schema_version`. | Must | Implemented |
| `SHR-3` | Every message model **shall** be immutable after construction. | Must | Implemented |
| `SHR-4` | Every message model **shall** survive a JSON serialise-deserialise round trip with no data loss. | Must | Implemented |
| `SHR-5` | The library **shall** reject a naive datetime on any message field, requiring timezone-aware UTC. | Must | Implemented |
| `SHR-6` | The library **shall** reject a `correlation_id` that is not a valid UUID. | Must | Implemented |
| `SHR-7` | The library **shall** default `schema_version` to `"1.0"`. | Must | Implemented |
| `SHR-8` | The library **shall** provide byte-level serialisation helpers for AMQP message bodies. | Must | Implemented |
| `SHR-9` | The library **shall** define `Direction`, `Magnitude`, `Horizon`, `EventType`, `ExtractionMethod`, `DecisionMethod`, `PriceKind`, `LlmStatus`, `EventPolarity`, `ConditionCode`, and `RoutingKey` as string enums shared by all services. | Must | Implemented |
| `SHR-10` | The library **shall** constrain `confidence`, `current_weight`, and `influence_weight` to `[0.0, 1.0]`. | Must | Implemented |
| `SHR-11` | The library **shall** constrain a `CloseObservation.close` to a positive decimal. | Must | Implemented |
| `SHR-12` | The library **shall** bound `PredictionMade.rationale` to 2000 characters. | Must | Implemented |

### 5.2 Asset registry

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-13` | The library **shall** load the asset registry from a JSON file, so adding an asset requires no code change. | Must | Implemented |
| `SHR-14` | The library **shall** read the registry path from `ASSET_REGISTRY_PATH`, falling back to the packaged default. | Must | Implemented |
| `SHR-15` | `AssetId` **shall** validate against the loaded registry and reject an unknown ID at construction. | Must | Implemented |
| `SHR-16` | `AssetId` **shall** subclass `str`, so an asset ID serialises as a plain string on the wire. | Must | Implemented |
| `SHR-17` | The loader **shall** refuse to start on a malformed registry rather than falling back to partial or stale data. | Must | Implemented |
| `SHR-18` | The loader **shall** reject a duplicate `asset_id`, a colon inside an ID, a keyword claimed by two assets, an unknown provider, an invalid session time, a `group_id`/`asset_id` collision, an empty group, and a fallback naming an undeclared asset. | Must | Implemented |
| `SHR-19` | The registry **shall** load once per process, so all messages in one process lifetime validate against one asset set. | Must | Implemented |
| `SHR-20` | The registry **shall** place every asset in exactly one asset group, commodities included. | Must | Implemented |
| `SHR-21` | The library **shall** provide accessors to resolve an asset, its group, a group's members, and an asset's provider. | Must | Implemented |
| `SHR-22` | The registry **shall** declare for every asset: provider, provider symbol, currency, timezone, session completion clock, price kind, adjustment flag, and fallback or explicit `null`. | Must | Implemented |

### 5.3 Messaging client

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-23` | The client **shall** publish to the `feed.events` topic exchange by routing key. | Must | Implemented |
| `SHR-24` | The client **shall** consume from an explicitly named queue that the calling service owns. | Must | Implemented |
| `SHR-25` | The client **shall** publish messages as persistent. | Must | Implemented |
| `SHR-26` | The client **shall** apply a bounded prefetch count so one consumer cannot take unbounded work. | Must | Implemented |
| `SHR-27` | The client **shall** support graceful shutdown that stops consumption before closing connections. | Must | Implemented |

### 5.4 LLM gateway

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-28` | The gateway **shall** select its provider adapter by the configured provider name. | Must | Implemented |
| `SHR-29` | The gateway **shall** fail with a configuration error when the provider, model, or API key is not configured. | Must | Implemented |
| `SHR-30` | The gateway **shall** reject a request whose estimated input exceeds `LLM_MAX_INPUT_TOKENS` **before** calling the provider. | Must | Implemented |
| `SHR-31` | The gateway **shall** estimate input size locally and **shall not** call the model to count tokens. | Must | Implemented |
| `SHR-32` | The gateway **shall** validate every response against the caller's JSON schema. | Must | Implemented |
| `SHR-33` | The gateway **shall** retry malformed structured output at most once. | Must | Implemented |
| `SHR-34` | The gateway **shall** cache results by provider, model, task, prompt version, output schema, and context hash. | Must | Implemented |
| `SHR-35` | A cache hit **shall** make zero provider calls and **shall** report `cache_hit = true` with `attempt_count = 0`. | Must | Implemented |
| `SHR-36` | The gateway **shall** record provider, model, prompt version, context hash, input tokens, output tokens, latency, attempt count, and status from the provider response. | Must | Implemented |
| `SHR-37` | The gateway **shall** treat a transport error as terminal for that call, leaving transport retries to the adapter. | Must | Implemented |
| `SHR-38` | The gateway **shall** apply `LLM_MAX_OUTPUT_TOKENS` and `LLM_TIMEOUT_SECONDS` to every provider call. | Must | Implemented |

### 5.5 Graph client

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-39` | The client **shall** connect to Neo4j over Bolt using `NEO4J_*` configuration. | Must | Implemented |
| `SHR-40` | The client **shall** return firing `CAUSES` edges for a given asset and set of event types. | Must | Implemented |
| `SHR-41` | The client **shall** return an unconditional edge unconditionally, and a conditioned edge only when its condition is active. | Must | Implemented |
| `SHR-42` | The client **shall** return an asset's own edge in preference to its group's edge for the same factor and condition. | Must | Implemented |
| `SHR-43` | The client **shall** return an inherited group edge whose `edge_id` names the group, not the asset. | Must | Implemented |
| `SHR-44` | The client **shall** compute each edge's reliability as `alpha / (alpha + beta)`. | Must | Implemented |
| `SHR-45` | The client **shall** bound connection acquisition, so a graph outage surfaces promptly instead of hanging. | Must | Implemented |
| `SHR-46` | The client **shall** support an idempotent edge weight update for Credibility. | Must | Implemented |
| `SHR-76` | The weight update **shall** target either an `:Asset` or an `:AssetGroup` edge, selected by the caller, so credit for an inherited edge lands on the industry prior that fired (`SHR-43`). | Must | Implemented |
| `SHR-77` | The client **shall** return the current `(alpha, beta)` of one group edge addressed directly by `(factor, group, condition)`, because a lookup through a member asset would miss a group edge that the member overrides. | Must | Implemented |
| `SHR-78` | A weight update naming an edge that does not exist **shall** raise, identifying whether an `:Asset` or an `:AssetGroup` target was expected. | Must | Implemented |

### 5.6 Session calendar

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-47` | The calendar **shall** resolve any IANA timezone through the standard library tz database. | Must | Implemented |
| `SHR-48` | The calendar **shall** reject an unknown timezone name rather than silently mishandling it. | Must | Implemented |
| `SHR-49` | The calendar **shall** treat any weekday as a trading session and **shall not** maintain a holiday list. | Must | Implemented |
| `SHR-50` | The calendar **shall** treat a session as complete at its market's local closing wall-clock on the session date. | Must | Implemented |
| `SHR-51` | The calendar **shall** resolve a baseline session as the most recent session already complete at the decision time, with no look-ahead. | Must | Implemented |
| `SHR-52` | The calendar **shall** resolve a settlement session as the next trading session after the baseline. | Must | Implemented |
| `SHR-53` | The calendar **shall** map a provider bar timestamp to its local-market session date. | Must | Implemented |
| `SHR-54` | The calendar **shall** derive daylight-saving offsets from the tz database, not from hardcoded rules. | Must | Implemented |

### 5.7 Logging

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-55` | The library **shall** emit logs as JSON lines. | Must | Implemented |
| `SHR-56` | Every log entry **shall** include the correlation ID when one is bound to the context. | Must | Implemented |
| `SHR-57` | The library **shall** provide middleware that binds a correlation ID for the lifetime of one message or request. | Must | Implemented |
| `SHR-58` | The library **shall** read its log threshold from `LOG_LEVEL`. | Must | Implemented |

### 5.8 Infrastructure

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-59` | `docker compose up` **shall** start PostgreSQL, Neo4j, and RabbitMQ and pass their health checks. | Must | Implemented |
| `SHR-60` | Infrastructure initialisation **shall** create one database `feed` containing the schemas `ingestion`, `cleansing`, `prediction`, `market_data`, `verification`, and `credibility`. | Must | Implemented |
| `SHR-61` | Infrastructure initialisation **shall** enable the `vector` extension. | Must | Implemented |
| `SHR-62` | Infrastructure initialisation **shall not** create application tables; each service owns its own DDL. | Must | Implemented |
| `SHR-63` | Infrastructure initialisation **shall** declare the durable topic exchange `feed.events`. | Must | Implemented |
| `SHR-64` | Infrastructure initialisation **shall** declare every canonical work queue as durable with its binding. | Must | Implemented |
| `SHR-65` | Infrastructure initialisation **shall** declare `feed.dlx` and one `<queue>.dlq` per durable work queue. | Must | Implemented |
| `SHR-66` | Infrastructure initialisation **shall** seed asset nodes, group nodes, `MEMBER_OF` relationships, causal factor nodes, and `CAUSES` edges. | Must | Implemented |
| `SHR-67` | Every seeded `CAUSES` edge **shall** start at `alpha = 1.0`, `beta = 1.0`. | Must | Implemented |
| `SHR-68` | The Neo4j asset seed **shall** be generated from the asset registry, so the two cannot drift. | Must | Implemented |

## 6. Non-functional requirements

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SHR-69` | The library **shall** install as an editable package via `pip install -e src/shared/`. | Must | Implemented |
| `SHR-70` | The library **shall** require Python 3.12 or later. | Must | Implemented |
| `SHR-71` | The library **shall** pass `ruff check` with no violations. | Must | Implemented |
| `SHR-72` | The library **shall** pass `mypy --strict` with no errors. | Must | Implemented |
| `SHR-73` | The library **shall not** import from any service package, so no dependency cycle exists. | Must | Implemented |
| `SHR-74` | All six services **shall** share one root virtual environment and one editable `shared` install. | Must | Implemented |
| `SHR-75` | The library **shall not** log or expose an API key value or name. | Must | Implemented |

## 7. How it works

### 7.1 Message validation

**Purpose:** make an invalid message impossible to construct, so a bad value cannot reach a database.

Every model inherits `FeedMessage`, which is configured frozen. Validation happens at construction,
so a service that builds a message with a bad asset ID fails immediately rather than publishing it.

Layers of validation, in order:

1. **Type** — Pydantic checks each field's declared type.
2. **Constraint** — `Field` bounds: confidence in `[0,1]`, close `> 0`, rationale ≤ 2000 chars,
   language matching `^[a-z]{2}$`, country matching `^[A-Z]{2}$`.
3. **Registry** — `AssetId` checks membership in the loaded registry.
4. **Timezone** — a datetime field requires `tzinfo`; a naive value is rejected.

Serialisation for AMQP is a pair of helpers: model → UTF-8 JSON bytes, and bytes → model.

**Why frozen:** a message object flows through a pipeline. If any stage could mutate it, the value
written to the database might differ from the value published — a defect that is very hard to trace.

### 7.2 Asset registry loading

**Purpose:** let a company or a whole market be added by editing one JSON file.

**Steps:**

1. At import, resolve the registry path: `ASSET_REGISTRY_PATH` if set, else the packaged
   `assets.json` inside the wheel.
2. Parse the file → [loader.py](../src/shared/shared/reference/loader.py)
3. Validate structure. Any of these is fatal:
   - duplicate `asset_id`
   - a colon inside an `asset_id`
   - one keyword claimed by two assets
   - an unknown provider
   - an invalid `session_complete_at`
   - a `group_id` colliding with an `asset_id`
   - an empty group
   - a `fallback` naming an undeclared asset
4. Build lookup maps: asset → entry, asset → group, group → members.
5. Register the valid ID set with `AssetId`.

**Rules:**

- The registry loads **once**. Reloading mid-flight would let one message validate against a
  different asset set than the one that produced it.
- The loader **refuses to boot** on a malformed file. A silent fallback would be worse: you would
  believe an edit took effect while the service ran on stale data.
- A structural check cannot catch a typo'd ticker — the registry would load, the asset would predict,
  and scoring would stall on a missing price. `python scripts/validate-assets.py` probes every
  provider symbol and cross-checks the reported currency and timezone.

**Registry field meanings:**

| Field | Purpose |
|---|---|
| `asset_id` | The canonical business ID. Upper snake case, no colon. Stored as `TEXT` in Postgres and `Asset.id` in Neo4j |
| `code` | Human-readable `EXCHANGE:TICKER`. **Documentation only** — never sent to a provider |
| `provider` | Selects the Market Data adapter |
| `provider_symbol` | The exact string that provider expects — differs per vendor for the same instrument |
| `keywords` | Company-scope inference keywords. Must be unique across all assets |
| `industry_keywords` | Group-scope keywords; a match fans out to every member |
| `currency`, `timezone`, `session_complete_at` | Required for scoring and the session calendar |
| `price_kind` | `PROVIDER_DAILY_CLOSE` or `OFFICIAL_SETTLEMENT` |
| `is_adjusted` | Whether closes are split/dividend adjusted |
| `fallback` | An economically equivalent asset, or explicit `null` |

### 7.3 Publishing and consuming

**Publishing:**

1. The service serialises the message to JSON bytes.
2. The client publishes to `feed.events` with the message's routing key and the persistent delivery
   mode → [messaging/client.py](../src/shared/shared/messaging/client.py)
3. The exchange routes by binding to every matching queue.

**Consuming:**

1. The service names the queue it owns. The client looks the queue up — it does not declare it,
   because topology is owned by the infrastructure definitions.
2. Prefetch bounds how many unacknowledged messages one consumer holds.
3. The callback processes the message, then acknowledges.
4. On shutdown, consumption stops first, in-flight work finishes or requeues, then connections close.

**Why lookup, not declare:** if each service declared its own queue, two services could declare the
same queue with different arguments and the second would fail at runtime. One definitions file makes
the topology reviewable.

### 7.4 LLM gateway call sequence

**Purpose:** one controlled path for every LLM call, so cost and correctness rules cannot be bypassed.

**Steps** → [llm/gateway.py](../src/shared/shared/llm/gateway.py)

1. Select the provider adapter by configured name. Missing provider, model, or key → configuration
   error, no call.
2. Validate the caller's output schema is a usable JSON schema.
3. Estimate input size locally, roughly 4 characters per token. Over budget → error **before** any
   provider call. The model is never asked to count tokens.
4. Build the cache key from provider, model, task, prompt version, output schema, and context hash.
5. On a cache hit, return immediately with `cache_hit = true` and `attempt_count = 0`. **Zero
   provider calls.**
6. On a miss, call the provider with the output token cap and timeout.
7. Validate the response against the schema.
8. On malformed output, retry — **once**, hard-capped by configuration.
9. Cache the valid result and return it with full usage metadata.

**Rules:**

- A transport error is terminal at this layer. Transport retries belong to the adapter, so a
  malformed-output retry budget is never consumed by a network problem.
- Usage metadata always comes from the provider's own response. Recording it costs no extra tokens.
- Only Cleansing may call this gateway. Prediction must not.

### 7.5 Graph traversal

**Purpose:** answer one question — which causal edges fire for this asset given these event types?

**Steps** → [graph/client.py](../src/shared/shared/graph/client.py)

1. Take the canonical asset ID, the event types present in the context, and the active condition
   codes.
2. Match `CausalFactor` nodes for those event types.
3. Follow `CAUSES` edges to the asset **and** to the asset's group.
4. Filter by condition:
   - edge with no `condition` → always fires
   - edge with a `condition` → fires only if that condition is active
5. Resolve inheritance: if the asset has its own edge for a `(factor, condition)` pair, use it and
   discard the group's. Otherwise use the group's, keeping the group ID in the `edge_id`.
6. Compute reliability as `alpha / (alpha + beta)`.
7. Return each edge with its ID, direction, expert weight, and reliability.

**Rules:**

- An asset-level edge **overrides** its group's; it never adds to it.
- An inherited edge's `edge_id` names the **group**, so Credibility updates the industry prior that
  actually fired instead of creating a per-asset edge that was never seeded.
- An unmapped event type returns no edges. `OTHER` has no `CausalFactor` node, so it produces no
  prediction — intentionally, since it is a catch-all for events with no modelled causal path.

### 7.6 Session resolution

**Purpose:** decide which two closes score a prediction, using the asset's own market calendar.

**Baseline and settlement** → [calendar/sessions.py](../src/shared/shared/calendar/sessions.py)

1. Convert the decision time to UTC, then to the asset's local date.
2. Walk backwards to the nearest weekday — that is the candidate baseline.
3. **No look-ahead check:** if that session has not yet reached its local completion clock, step back
   to the previous session. A prediction made at 10:00 local cannot use today's close, because it
   does not exist yet.
4. Settlement is the next weekday after the baseline.

**Completion:** a session is complete at its market's local closing wall-clock — 17:00 for New York,
18:00 for Stockholm, taken from the registry's `session_complete_at`.

**Worked example:** a prediction at 14:00 UTC on Friday 2026-03-13 for a Stockholm listing.

- Local date is Friday 2026-03-13; Stockholm is `UTC+1`, so local time is 15:00.
- Stockholm completes at 18:00 local, so Friday's close does not exist yet.
- Baseline steps back to Thursday 2026-03-12.
- Settlement is the next weekday after Thursday: Friday 2026-03-13.

**Rules:**

- Any weekday is a session. Holidays are not modelled — a holiday appears as a missing provider bar
  and the price request stays pending and retries.
- Daylight saving comes from the tz database. On 2026-03-10 New York is already `UTC-4` while
  Stockholm is still `UTC+1`, so one hand-rolled rule could not serve both markets.

### 7.7 Correlation ID propagation

**Purpose:** trace one piece of news across all six services in the logs.

1. Ingestion generates a `correlation_id` when it creates `ArticleIngested`.
2. Every downstream service copies it unchanged onto the message it publishes.
3. The logging middleware binds it for the lifetime of the message being handled.
4. Every log line emitted during that work carries it automatically.

Result: one `correlation_id` filter shows the entire journey from article to learned weight.

## 8. Interfaces

### 8.1 Message envelope

Defined on `FeedMessage`, inherited by all six messages.

| Field | Type | Default | Purpose |
|---|---|---|---|
| `message_id` | UUID | generated | Unique delivery identity, used for idempotency |
| `correlation_id` | UUID | required | Business-flow identity, constant end to end |
| `causation_id` | UUID or null | `null` | The message that caused this one |
| `occurred_at` | UTC datetime | required | When the domain event happened |
| `schema_version` | string | `"1.0"` | Contract version |

### 8.2 Shared enums

| Enum | Values |
|---|---|
| `Direction` | `UP`, `DOWN`, `NEUTRAL` |
| `Magnitude` | `SMALL`, `MEDIUM`, `LARGE` |
| `Horizon` | `ONE_TRADING_DAY` |
| `ExtractionMethod` | `LOCAL`, `LLM_ASSISTED` |
| `DecisionMethod` | `GRAPH_ONLY`, `LLM_ARBITRATED` |
| `PriceKind` | `PROVIDER_DAILY_CLOSE`, `OFFICIAL_SETTLEMENT` |
| `LlmStatus` | `SUCCESS`, `FAILED` |
| `EventPolarity` | `OCCURRENCE`, `RESOLUTION` |
| `ConditionCode` | `TRANSPORT_AFFECTED`, `SAFE_HAVEN_ONLY`, `RISK_PREMIUM_ELEVATED` |
| `EventType` | 32 values — see [REF-01-event-taxonomy.md](REF-01-event-taxonomy.md) |
| `RoutingKey` | `article.ingested`, `event.detected`, `prediction.made`, `price.requested`, `price.observed`, `prediction.scored` |

### 8.3 Shared nested types

**`SourceRef`** — provenance for one article inside an event.

| Field | Type |
|---|---|
| `article_id` | UUID |
| `source_id` | non-empty string |
| `canonical_url` | HTTP URL |
| `title` | non-empty string |
| `published_at` | UTC datetime |

**`FactConflict`** — a field on which sources disagree.

| Field | Type |
|---|---|
| `field` | non-empty string |
| `values` | list of `{source_id, value}` |
| `resolution` | string or null |

**`LlmMetadata`** — usage record for one LLM call.

| Field | Type | Constraint |
|---|---|---|
| `prompt_version` | non-empty string | |
| `model` | non-empty string | |
| `context_hash` | non-empty string | |
| `input_tokens` | integer | `>= 0` |
| `output_tokens` | integer | `>= 0` |
| `latency_ms` | integer | `>= 0` |
| `attempt_count` | integer | `>= 1` |
| `status` | `LlmStatus` | |

**`ContributingEdge`** — one causal edge's contribution to a decision.

| Field | Type | Constraint |
|---|---|---|
| `edge_id` | non-empty string | `FACTOR->TARGET` or `FACTOR\|CONDITION->TARGET` |
| `direction` | `Direction` | |
| `current_weight` | float | `[0,1]` — the edge's learned reliability |
| `influence_weight` | float | `[0,1]` — the edge's expert weight |
| `path` | non-empty string | Compact provenance |

**`CloseObservation`** — one immutable closing price with full provenance.

| Field | Type | Constraint |
|---|---|---|
| `session` | date | Local market session |
| `close` | Decimal | `> 0` |
| `provider_bar_time` | aware datetime or null | The provider's own timestamp |
| `fetched_at` | UTC datetime | When the adapter acquired it |
| `source` | non-empty string | Provider name |
| `provider_symbol` | non-empty string | Vendor ticker |
| `price_kind` | `PriceKind` | |
| `is_adjusted` | bool | |
| `registry_version` | non-empty string | Registry snapshot used |

### 8.4 RabbitMQ topology

Declared in [infra/rabbitmq/definitions.json](../infra/rabbitmq/definitions.json).

| Exchange | Type | Durable |
|---|---|---|
| `feed.events` | topic | yes |
| `feed.dlx` | topic | yes |

| Queue | Bound routing key | Durable | DLQ |
|---|---|---|---|
| `cleansing.articles` | `article.ingested` | yes | `cleansing.articles.dlq` |
| `prediction.events` | `event.detected` | yes | `prediction.events.dlq` |
| `verification.predictions` | `prediction.made` | yes | `verification.predictions.dlq` |
| `verification.prices` | `price.observed` | yes | `verification.prices.dlq` |
| `market-data.price-requests` | `price.requested` | yes | `market-data.price-requests.dlq` |
| `credibility.scored` | `prediction.scored` | yes | `credibility.scored.dlq` |
| `gateway.predictions.live` | `prediction.made` | no | — |
| `gateway.scored.live` | `prediction.scored` | no | — |

Gateway live queues are non-durable and auto-delete because REST supplies catch-up state.

### 8.5 Neo4j seed scripts

| File | Purpose |
|---|---|
| [01-constraints-indexes.cypher](../infra/neo4j/init/01-constraints-indexes.cypher) | Uniqueness constraints and indexes |
| [02-seed-assets.cypher](../infra/neo4j/init/02-seed-assets.cypher) | Asset and group nodes, `MEMBER_OF` — generated from the registry |
| [03-seed-causal-factors.cypher](../infra/neo4j/init/03-seed-causal-factors.cypher) | One node per event type |
| [04-seed-causal-edges.cypher](../infra/neo4j/init/04-seed-causal-edges.cypher) | Expert unconditional edges |
| [05-seed-conditioned-edges.cypher](../infra/neo4j/init/05-seed-conditioned-edges.cypher) | Condition-qualified edges |
| [06-seed-group-edges.cypher](../infra/neo4j/init/06-seed-group-edges.cypher) | Industry priors, inherited by members |
| [07-seed-new-event-type-edges.cypher](../infra/neo4j/init/07-seed-new-event-type-edges.cypher) | Edges for later taxonomy additions |

### 8.6 Helper scripts

| Command | Purpose |
|---|---|
| `powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1` | Create the root virtual environment (Windows) |
| `bash scripts/setup-venv.sh` | Create the root virtual environment (macOS/Linux) |
| `python scripts/validate-assets.py` | Validate the registry structure and probe live providers |
| `python scripts/validate-assets.py --offline` | Structure only, for CI |
| `python scripts/generate-asset-seed.py` | Regenerate the Neo4j asset seed from the registry |
| `python scripts/generate-asset-seed.py --check` | Assert the seed matches the registry |

## 9. Data design

### 9.1 PostgreSQL initialisation

[infra/postgres/01-init-database.sql](../infra/postgres/01-init-database.sql) runs once on first
container start, inside the `POSTGRES_DB` database as the superuser.

It does exactly three things:

1. `CREATE EXTENSION IF NOT EXISTS vector` — pgvector, used by `cleansing` for 1024-dimension
   embeddings.
2. Creates six schemas with `AUTHORIZATION feed_user`.
3. Nothing else.

**It creates no application tables.** Each service owns its own table DDL and applies it
idempotently at startup, so a service's schema evolves with the service that owns it.

| Schema | Owner |
|---|---|
| `ingestion` | Ingestion |
| `cleansing` | Cleansing |
| `prediction` | Prediction |
| `market_data` | Market Data |
| `verification` | Verification |
| `credibility` | Credibility |

### 9.2 Neo4j graph model

```text
(:CausalFactor {id})-[:CAUSES {condition, direction, weight, confidence,
                               alpha, beta, last_updated}]->(:Asset {id})

(:CausalFactor {id})-[:CAUSES {...}]->(:AssetGroup {id})

(:Asset {id})-[:MEMBER_OF]->(:AssetGroup {id})
```

**`CAUSES` edge properties:**

| Property | Meaning |
|---|---|
| `condition` | A `ConditionCode`, or absent for an unconditional edge |
| `direction` | `UP`, `DOWN`, or `NEUTRAL` |
| `weight` | Expert-assigned influence in `[0,1]` |
| `confidence` | Expert confidence in the edge |
| `alpha` | Beta-Bernoulli success count, starts at `1.0` |
| `beta` | Beta-Bernoulli failure count, starts at `1.0` |
| `last_updated` | When learning last touched this edge |

Each `(factor, target, condition)` triple is a distinct edge with its own weight and its own
`alpha`/`beta`, so evidence for a conditioned edge is never collapsed into the unconditional one.

### 9.3 Asset registry file

Loaded from [infra/assets/assets.json](../infra/assets/assets.json) when mounted, else the packaged
copy at `src/shared/shared/reference/assets.json`.

Current version `multi-market-v2`: 16 groups, 37 assets (34 primary + 3 fallback entries),
4 currencies, 6 markets. Provider routing, session calendars, and the version history are in
[REF-02-asset-registry.md](REF-02-asset-registry.md).

```json
{
  "registry_version": "multi-market-v2",
  "rollover_policy": "PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1",
  "groups": [
    {
      "group_id": "WEAPON_INDUSTRY",
      "display_name": "Weapon & defence industry",
      "industry_keywords": ["defense", "defence", "missile"],
      "assets": [
        {
          "asset_id": "SAAB_B_STO",
          "name": "Saab AB",
          "code": "STO:SAAB-B",
          "provider": "yahoo",
          "provider_symbol": "SAAB-B.ST",
          "economic_identity": "Saab AB ordinary shares (STO)",
          "expected_exchange": "STO",
          "currency": "SEK",
          "timezone": "Europe/Stockholm",
          "session_complete_at": "18:00",
          "price_kind": "PROVIDER_DAILY_CLOSE",
          "is_adjusted": false,
          "fallback": null,
          "keywords": ["saab", "saab-b"]
        }
      ]
    }
  ]
}
```

## 10. Configuration

### 10.1 PostgreSQL

| Variable | Default | Effect |
|---|---|---|
| `POSTGRES_USER` | `feed_user` | Role owning all six schemas |
| `POSTGRES_PASSWORD` | — | Required |
| `POSTGRES_DB` | `feed` | The single database name |
| `DATABASE_URL` | `postgresql://feed_user:...@localhost:5432/feed` | Connection string every service reads |

### 10.2 Neo4j — prefix `NEO4J_`

| Variable | Default | Effect |
|---|---|---|
| `NEO4J_AUTH` | `neo4j/feedpassword` | Container credentials, `user/password` form |
| `NEO4J_PASSWORD` | `feedpassword` | Used by the seed container |
| `NEO4J_URI` | `bolt://localhost:7687` | Bolt endpoint |
| `NEO4J_USER` | `neo4j` | Client user |
| `NEO4J_CONNECTION_TIMEOUT_SECONDS` | `10.0` | Bounded acquisition so an outage surfaces promptly |
| `NEO4J_MAX_CONNECTION_POOL_SIZE` | `20` | Client pool ceiling |

### 10.3 RabbitMQ — prefix `RABBITMQ_`

| Variable | Default | Effect |
|---|---|---|
| `RABBITMQ_DEFAULT_USER` | `feed_user` | Broker user |
| `RABBITMQ_DEFAULT_PASS` | — | Broker password |
| `RABBITMQ_URL` | `amqp://feed_user:...@localhost:5672/` | Client connection |
| `RABBITMQ_MAX_RETRIES` | `3` | Bounded publish retries |
| `RABBITMQ_PREFETCH_COUNT` | `10` | Unacknowledged messages per consumer |
| `RABBITMQ_CHANNEL_POOL_SIZE` | `10` | Publisher channel pool |

### 10.4 LLM gateway — prefix `LLM_`

| Variable | Default | Effect |
|---|---|---|
| `LLM_PROVIDER` | empty | Selects the adapter. `openai` covers any OpenAI-compatible endpoint. Empty disables LLM paths |
| `LLM_MODEL` | empty | Provider model ID |
| `LLM_API_KEY` | empty | Secret. Empty disables LLM-assisted paths |
| `LLM_BASE_URL` | empty | Custom endpoint for a non-default OpenAI-compatible provider |
| `LLM_MAX_INPUT_TOKENS` | `6000` | Input budget, enforced before the provider call |
| `LLM_MAX_OUTPUT_TOKENS` | `512` | Output cap per call |
| `LLM_TIMEOUT_SECONDS` | `30.0` | Per-call timeout |
| `LLM_TRANSPORT_RETRIES` | `2` | Adapter-level transport retries, max 5 |
| `LLM_MALFORMED_OUTPUT_RETRIES` | `1` | Structured-output retries. **Hard-capped at 1** |
| `LLM_CACHE_ENABLED` | `true` | When true, a replayed request makes zero provider calls |

### 10.5 Reference data and logging

| Variable | Default | Effect |
|---|---|---|
| `ASSET_REGISTRY_PATH` | packaged `assets.json` | Overrides the registry file; mounted read-only at `/config/assets.json` |
| `LOG_LEVEL` | `INFO` | Structured log threshold |

## 11. Verification

| Requirement | Method | Evidence |
|---|---|---|
| `SHR-1`…`SHR-12` | Test | [test_schemas.py](../src/shared/tests/test_schemas.py) — all six models, round trip, frozen, envelope, constraints |
| `SHR-13`, `SHR-14`, `SHR-17`, `SHR-18`, `SHR-19` | Test | [test_asset_loader.py](../src/shared/tests/test_asset_loader.py) — JSON loading, malformed-file refusal, each validation rule |
| `SHR-15`, `SHR-16` | Test | [test_asset_id.py](../src/shared/tests/test_asset_id.py) — registry validation, `str` behaviour, unknown ID rejected |
| `SHR-20`, `SHR-21`, `SHR-22` | Test | [test_asset_registry.py](../src/shared/tests/test_asset_registry.py) — group membership, accessors, required fields |
| `SHR-23`…`SHR-27` | Test | [test_messaging.py](../src/shared/tests/test_messaging.py) — publish by routing key, consume owned queue, persistence, prefetch |
| `SHR-28`…`SHR-38` | Test | [test_llm_gateway.py](../src/shared/tests/test_llm_gateway.py) — provider selection, budget enforcement, schema validation, single retry, cache hit with zero calls, usage metadata |
| `SHR-39`…`SHR-46` | Test | [test_graph_client.py](../src/shared/tests/test_graph_client.py) — firing edges, condition gating, asset-over-group override, reliability, weight update |
| `SHR-76`…`SHR-78` | Test | [test_graph_client.py](../src/shared/tests/test_graph_client.py) — group-targeted update (conditional and unconditional), direct group count read, missing-edge error names the target label |
| `SHR-47`, `SHR-48`, `SHR-54` | Test | [test_calendar.py](../src/shared/tests/test_calendar.py) — IANA resolution, unknown zone rejected, DST from tz database |
| `SHR-49`…`SHR-53` | Test | [test_calendar_us_baseline.py](../src/shared/tests/test_calendar_us_baseline.py) — weekday sessions, completion clock, no look-ahead |
| `SHR-47`, `SHR-50` | Test | [test_calendar_multi_market.py](../src/shared/tests/test_calendar_multi_market.py) — Stockholm and New York resolve on their own clocks |
| `SHR-55`…`SHR-58` | Test | [test_logging.py](../src/shared/tests/test_logging.py) — JSON output, correlation ID present, middleware binding |
| `SHR-59`…`SHR-65` | Test | [test_infrastructure.py](../src/shared/tests/test_infrastructure.py) — containers healthy, schemas present, pgvector enabled, exchange/queues/DLQs declared durable |
| `SHR-66`, `SHR-67`, `SHR-68` | Test | [test_graph_seed_coverage.py](../src/shared/tests/test_graph_seed_coverage.py) — seed coverage, `1.0/1.0` priors, seed matches registry |
| `SHR-69`, `SHR-70` | Demonstration | `pip install -e src/shared/` succeeds on Python 3.12+ |
| `SHR-71`, `SHR-72` | Demonstration | `ruff check src/shared/` and `mypy src/shared/ --strict` |
| `SHR-73` | Inspection | No `import` of a service package anywhere under `src/shared/shared/` |
| `SHR-74` | Inspection | [scripts/setup-venv.sh](../scripts/setup-venv.sh) creates one root `.venv` |
| `SHR-75` | Inspection | [llm/settings.py](../src/shared/shared/llm/settings.py) — `require_api_key` reports absence without the value |

## 12. Failure handling

| Failure | Behaviour | Recovery |
|---|---|---|
| Registry file missing or malformed | Process refuses to start with a specific validation error | Fix the file, restart |
| Unknown asset ID in a message | `ValidationError` at construction; the message is never published | Add the asset to the registry, or fix the producer |
| Unknown timezone in the registry | `UnsupportedTimezoneError` | Correct the registry entry |
| PostgreSQL unavailable | Pool creation fails; readiness fails | Automatic on reconnection |
| Neo4j unavailable | Bounded connection timeout, then error — no hang | Automatic on reconnection |
| RabbitMQ unavailable | Publish fails; the caller's outbox retains the row | Outbox relay drains on reconnection |
| LLM provider not configured | `LLMConfigurationError` before any network call | Set `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` |
| LLM input over budget | `LLMConfigurationError` before any provider call | Reduce excerpt count or length |
| LLM output malformed twice | `LLMMalformedOutputError` after exactly one retry | Caller marks its work retryable; nothing is fabricated |
| LLM transport error | Raised immediately, terminal for that call | The caller retries the whole operation later |

## 13. Assumptions, dependencies, and known limitations

### 13.1 Assumptions

- Every service runs in one process with one registry load; the registry file does not change under a
  running process.
- The token estimate of roughly 4 characters per token is adequate for budget enforcement; exact
  counts arrive with the provider response.
- Docker Compose provides all three engines; there is no cloud-managed alternative configured.

### 13.2 Accepted design decisions

| Decision | Reason |
|---|---|
| `AssetId` is a validated string, not a closed enum | Adding a market must not require a code change. Trade-off accepted: `mypy` can no longer prove exhaustive coverage over assets |
| The registry loads once at startup | Reloading mid-flight would let one message validate against a different asset set than the one that produced it |
| The loader refuses to boot on a malformed file | A silent fallback would let you believe an edit took effect while the service ran on stale data |
| Messages are frozen | A mutable message could be changed between the database write and the publish |
| Queues are declared by infrastructure, not by services | Two services declaring one queue with different arguments would fail at runtime |
| Malformed-output retries are capped at 1 in the type itself | Makes an expensive misconfiguration impossible rather than merely discouraged |

### 13.3 Known limitations

| Limitation | Consequence |
|---|---|
| Structural registry validation cannot catch a typo'd ticker | The asset loads and predicts, then scoring stalls on a missing price. Run `validate-assets.py` with the live probe |
| Holidays are not modelled | A holiday consumes a price request's retry budget instead of skipping to the next real session |
| The LLM cache is in-memory | A restart loses cache entries, so a replayed request after restart calls the provider again |
| `mypy --strict` cannot prove exhaustive asset coverage | Accepted trade-off of the data-driven registry |
| Default embedding and NLP backends are deterministic stand-ins | `hashing` and `keyword` let tests run without multi-gigabyte models; real models need the `ml` extra |

## 14. How to update this document

Follow the rules in [README.md](README.md#how-to-update-these-documents).

Component-specific notes:

- **Changing a message model** is a contract change. Update section 8, the affected consumer's SRS,
  and [SyRS §8](SyRS-system.md#8-interfaces). Adding an optional field with a default is backward
  compatible; removing, renaming, or retyping a field requires a major version.
- **Adding an asset** is a registry edit, not a change to this document — but regenerate the Neo4j
  seed (`python scripts/generate-asset-seed.py`) and update the counts in section 9.3.
- **Adding a queue or routing key** means updating section 8.4 here,
  [infra/rabbitmq/definitions.json](../infra/rabbitmq/definitions.json), and
  [SyRS §7.1](SyRS-system.md#71-routing-topology).
- **Adding an LLM provider** means a new adapter plus a registry entry in `shared.llm.providers`;
  update section 10.4 if it needs new configuration.
- **Changing session or calendar logic** affects both Verification and Market Data. Update
  [SRS-05](SRS-05-market-data.md) and [SRS-06](SRS-06-verification.md) together — the semantics must
  stay identical, which is why the logic lives here once.

## 15. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| `2026-08-05` | `1.0.0` | Initial specification, written from the implemented code | E01 complete; replaces the E01 epic and task files |
| `2026-08-07` | `1.1.0` | Added `SHR-76`…`SHR-78`: the graph client's weight update can target an `:AssetGroup`, and group counts are readable directly. `update_edge_weight`'s Cypher parameter renamed `asset_id` → `target_id` | Credibility could not apply learning from inherited group edges (see SRS-07 change history) |
