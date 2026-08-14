# SyRS: Feed Analyzer System

## 1. Document control

| | |
|---|---|
| Document ID | `SyRS` |
| Scope | Whole system — seven implemented components plus one approved (Notification) |
| Requirement ID prefix | `SYS` |
| Status | `Implemented` (components 1–7); `Approved` (Notification); Gateway and Dashboard not built |
| Version | `1.3.0` |
| Last verified against code | `2026-08-12` |

## 2. Purpose and scope

### 2.1 What the system does

Feed Analyzer is a local proof of concept that tests one hypothesis:

> Can structured news events combined with a causal knowledge graph predict the next trading
> session's price direction for a given asset?

It reads news, groups articles describing the same real-world event, maps that event to causal
factors in a knowledge graph, produces a directional prediction (`UP` / `DOWN` / `NEUTRAL`) with a
confidence and magnitude, waits for the market to close, scores the prediction against the actual
price move, and feeds the outcome back to adjust the graph's edge weights.

The loop is: **news → event → prediction → price → score → learning.**

### 2.2 In scope

- Assets declared in the JSON asset registry: commodities plus company listings on US, Swedish,
  Danish, Dutch, French and German markets, in USD, SEK, EUR, and DKK.
- One prediction horizon: `ONE_TRADING_DAY`.
- Outcome vocabulary: direction `UP`/`DOWN`/`NEUTRAL`, magnitude `SMALL`/`MEDIUM`/`LARGE`, plus a
  confidence in `[0,1]`.
- Evaluation using immutable close-to-close price observations.
- Learning that adjusts causal edge weights from scored outcomes.
- Local-only deployment via Docker Compose.

### 2.3 Explicitly out of scope

| Out of scope | Reason |
|---|---|
| Executing trades | The system is an experiment, not a trading platform |
| Personalised financial advice | Not a regulated advisory product |
| Intraday or multi-day horizons | Only `ONE_TRADING_DAY` is validated |
| Public internet deployment | Security boundary is local-only |
| Authentication, rate limiting, external CORS | Deferred until external deployment is approved |
| Prediction-time LLM arbitration | Blocked by the POC-6 `STOP` result (see 13.2) |
| Automated backup, restore, deletion workflows | Deferred while the POC runs |
| Distributed tracing, load and soak testing | Deferred |

## 3. Definitions

| Term | Meaning |
|---|---|
| Article | One news item from one source, normalised and stored |
| Event | One real-world occurrence, built from one or more articles describing it |
| Cluster | The working set of articles believed to describe one event, before the event is published |
| Causal factor | A node in the knowledge graph representing a kind of event, e.g. `MILITARY_CONFLICT` |
| `CAUSES` edge | A directed, weighted link from a causal factor to an asset or asset group |
| Firing edge | A `CAUSES` edge whose conditions are satisfied, so it contributes force to a decision |
| `CORRELATES_WITH` edge | A directed, weighted link from one asset to another, always gated by a condition (`UPSTREAM_UP` or `UPSTREAM_DOWN`); fires when the source asset was predicted directional in the same pipeline run |
| Propagation pass | A further `decide()` sweep in Prediction over assets reachable by `CORRELATES_WITH` edges from the assets decided in pass 0 |
| Visited set | Per-pipeline-run set of asset IDs already decided; prevents cycle re-entry |
| `propagation_depth` | Integer on `PredictionMade`: `0` = direct, from a fired `CAUSES` edge; `1+` = the number of `CORRELATES_WITH` hops that produced it |
| `propagation_chain` | Ordered list of `PropagationHop` values on `PredictionMade` and `PredictionScored`; empty for a direct prediction |
| `PropagationHop` | Frozen value model: `(source_asset_id, target_asset_id, condition, direction, edge_weight)` |
| Context | A time window grouping the events that affect one asset, used for one prediction |
| Baseline session | The trading session whose close is the "before" price |
| Settlement session | The trading session whose close is the "after" price |
| Deadband | A price move too small to count as directional; treated as `NEUTRAL` |
| Asset group | An industry grouping used to fan out industry-wide news and to inherit causal edges |
| Canonical asset ID | The registry-validated business identifier, e.g. `GOLD`, `SAAB_B_STO` |
| Provider symbol | A vendor-specific ticker, e.g. `SAAB-B.ST` — never crosses a service boundary |
| Outbox | A database table holding messages to publish, so a commit and a publish cannot diverge |
| Beta-Bernoulli | The `alpha`/`beta` counter pair used to learn a reliability in `[0,1]` |
| DLQ | Dead-letter queue, holding messages that could not be processed |
| M1 | Milestone 1 — the current walking-skeleton scope |

## 4. System context

### 4.1 End-to-end flow

```text
   External news sources (RSS feeds, FreeNewsApi.io)
                    |
                    v
        +-------------------------+
        |   1. Ingestion          |  polls sources, normalises, stores
        +-------------------------+
                    | article.ingested
                    v
        +-------------------------+
        |   2. Cleansing          |  dedups, embeds, clusters, extracts event
        +-------------------------+
                    | event.detected
                    v
        +-------------------------+
        |   3. Prediction         |  groups events per asset, traverses graph
        +-------------------------+
                    | prediction.made
                    v
        +-------------------------+
        |   4. Verification       |  schedules evaluation, resolves sessions
        +-------------------------+
                    | price.requested
                    v
        +-------------------------+
        |   5. Market Data        |  fetches both closes from provider
        +-------------------------+
                    | price.observed
                    v
        +-------------------------+
        |   4. Verification       |  scores close-to-close
        +-------------------------+
                    | prediction.scored
                    v
        +-------------------------+
        |   6. Credibility        |  updates edge weights and source utility
        +-------------------------+
```

Verification appears twice because it owns both halves of evaluation: it creates the price request,
then scores the result.

### 4.2 Components

| # | Component | Responsibility | Postgres schema | Specification |
|---|---|---|---|---|
| 0 | Shared library | Message schemas, messaging client, LLM gateway, graph client, calendar, registry, logging | — | [SRS-01](SRS-01-shared-foundation.md) |
| 1 | Ingestion | Acquire and normalise articles | `ingestion` | [SRS-02](SRS-02-ingestion.md) |
| 2 | Cleansing | Turn articles into distinct events | `cleansing` | [SRS-03](SRS-03-cleansing.md) |
| 3 | Prediction | Turn events into predictions using the graph | `prediction` | [SRS-04](SRS-04-prediction.md) |
| 4 | Verification | Schedule evaluation and score outcomes | `verification` | [SRS-06](SRS-06-verification.md) |
| 5 | Market Data | Fetch provider closing prices | `market_data` | [SRS-05](SRS-05-market-data.md) |
| 6 | Credibility | Learn from scored outcomes | `credibility` | [SRS-07](SRS-07-credibility.md) |
| 7 | Notification | Dispatch prediction alerts via email and WhatsApp | None (file-based recipients) | [SRS-10](SRS-10-notification.md) |

### 4.3 Infrastructure

| Engine | Version | Purpose |
|---|---|---|
| PostgreSQL + pgvector | 16 | One database `feed`, six service-owned schemas; pgvector holds article embeddings |
| Neo4j | 5 | The causal knowledge graph and its learned edge weights |
| RabbitMQ | 3.13 | The durable topic exchange `feed.events` and all consumer queues |

## 5. Functional requirements

### 5.1 Product behaviour

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-1` | The system **shall** ingest news articles from configured external sources without manual intervention. | Must | Implemented |
| `SYS-2` | The system **shall** preserve causally distinct events as separate events, even when their text is semantically similar. | Must | Implemented |
| `SYS-3` | The system **shall** combine multiple concurrent events affecting the same asset into a single prediction decision. | Must | Implemented |
| `SYS-4` | The system **shall** produce a prediction that records which causal edges contributed to it, so the decision is explainable. | Must | Implemented |
| `SYS-5` | The system **shall** verify each prediction against immutable close-to-close market observations. | Must | Implemented |
| `SYS-6` | The system **shall** update learned causal evidence from scored outcomes without counting the same outcome twice. | Must | Implemented |
| `SYS-7` | The system **shall not** execute trades or issue personalised financial advice. | Must | Implemented |
| `SYS-8` | The system **shall** support exactly one prediction horizon, `ONE_TRADING_DAY`. | Must | Implemented |

### 5.2 Identity and reference data

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-9` | Every service boundary **shall** carry canonical asset IDs; a provider symbol **shall not** be used as a business identifier. | Must | Implemented |
| `SYS-10` | The system **shall** reject an asset ID that is not present in the loaded asset registry, at every message boundary. | Must | Implemented |
| `SYS-11` | The system **shall** accept only event types declared in the canonical event taxonomy. | Must | Implemented |
| `SYS-12` | Adding an asset to the registry **shall not** require a code change or a database migration. | Must | Implemented |
| `SYS-13` | The system **shall** load the asset registry once at process start, so all messages in one process lifetime validate against one asset set. | Must | Implemented |
| `SYS-14` | The system **shall** refuse to start when the asset registry file is malformed, rather than falling back to stale or partial data. | Must | Implemented |

### 5.3 Data ownership

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-15` | The system **shall** use one PostgreSQL database containing one schema per service. | Must | Implemented |
| `SYS-16` | A service **shall** write only to the schema it owns. | Must | Implemented |
| `SYS-17` | Each service **shall** own the table DDL for its schema and apply it idempotently at startup. | Must | Implemented |
| `SYS-18` | Neo4j **shall** store only the causal graph and its learned edge state. | Must | Implemented |
| `SYS-19` | Cross-service data access **shall** occur through messages, an approved read-only endpoint, or an approved read-only view — never by writing another service's schema. | Must | Implemented |

### 5.4 Messaging

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-20` | Services **shall** publish domain events to the durable topic exchange `feed.events` using canonical routing keys. | Must | Implemented |
| `SYS-21` | Each consumer **shall** consume its own dedicated queue and **shall not** consume another service's work queue. | Must | Implemented |
| `SYS-22` | Every durable work queue **shall** have a dedicated dead-letter queue named `<queue>.dlq`. | Must | Implemented |
| `SYS-23` | Every message **shall** carry `message_id`, `correlation_id`, `causation_id`, `occurred_at`, and `schema_version`. | Must | Implemented |
| `SYS-24` | A `correlation_id` **shall** propagate unchanged from the originating article through every downstream message. | Must | Implemented |
| `SYS-25` | A state change plus its message publication **shall** use a transactional outbox, so the two cannot diverge. | Must | Implemented |
| `SYS-26` | A consumer **shall** use a domain idempotency key so a redelivered message causes no duplicate state change. | Must | Implemented |
| `SYS-27` | A message with an unsupported major schema version **shall** be dead-lettered with failure metadata, not silently dropped. | Must | Implemented |
| `SYS-28` | Adding an optional field with a default **shall** remain backward compatible within major version 1. | Must | Implemented |

### 5.5 LLM usage policy

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-29` | The system **shall** attempt deterministic local processing before any LLM call. | Must | Implemented |
| `SYS-30` | Only the Cleansing Service **shall** call the LLM, and only for ambiguous extraction or factual conflict resolution. | Must | Implemented |
| `SYS-31` | The Prediction Service **shall** make zero LLM calls. | Must | Implemented |
| `SYS-32` | All LLM access **shall** route through the shared LLM gateway; no service **shall** call a provider SDK directly. | Must | Implemented |
| `SYS-33` | The LLM provider and model **shall** be selected by configuration, not by service code. | Must | Implemented |
| `SYS-34` | An LLM prompt **shall** contain compact structured fields and bounded excerpts, never raw HTML or a complete article. | Must | Implemented |
| `SYS-35` | The system **shall** record model, prompt version, context hash, input and output tokens, latency, attempt count, and status for every LLM call, taken from provider response metadata without an additional call. | Must | Implemented |
| `SYS-36` | A repeated identical LLM request **shall** be served from cache and make zero provider calls. | Must | Implemented |
| `SYS-37` | The system **shall** retry malformed structured LLM output at most once. | Must | Implemented |
| `SYS-38` | Application code **shall** generate all identifiers; the LLM **shall not** generate an identifier. | Must | Implemented |

### 5.6 Scoring rules

These rules are fixed system-wide so every prediction is scored identically.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-39` | Actual return **shall** be computed as `(settlement_close - baseline_close) / baseline_close`. | Must | Implemented |
| `SYS-40` | An absolute actual return below `0.003` **shall** be scored as direction `NEUTRAL`. | Must | Implemented |
| `SYS-41` | Outside the deadband, a positive return **shall** be `UP` and a negative return **shall** be `DOWN`. | Must | Implemented |
| `SYS-42` | Actual magnitude **shall** be `SMALL` below 1%, `MEDIUM` from 1% to below 3%, and `LARGE` at or above 3% absolute return. | Must | Implemented |
| `SYS-43` | `is_correct` **shall** be true only when the predicted direction equals the actual direction. | Must | Implemented |
| `SYS-44` | Both close observations and their full provider metadata **shall** be stored with every outcome. | Must | Implemented |
| `SYS-45` | A provider daily close **shall not** be described or stored as an official exchange settlement. | Must | Implemented |

### 5.7 Learning rules

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-46` | Every Beta-Bernoulli learned state **shall** begin at `alpha = 1.0`, `beta = 1.0`. | Must | Implemented |
| `SYS-47` | A processed-prediction ledger entry **shall** be written before any learning update is applied. | Must | Implemented |
| `SYS-48` | Edge directional validity, arbiter decision quality, and predictive source utility **shall** be learned as separate targets and **shall not** be collapsed into one score. | Must | Implemented |
| `SYS-49` | A causal edge **shall** be judged against the direction that edge itself predicted, not against whether the final prediction was correct. | Must | Implemented |
| `SYS-50` | Source scoring **shall** be named `predictive_source_utility` and **shall not** be presented as factual truthfulness or journalistic reliability. | Must | Implemented |
| `SYS-51` | Prediction confidence **shall** be recorded at prediction time for later calibration analysis. | Must | Implemented |

## 6. Non-functional requirements

### 6.1 Reliability

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-52` | Scheduled work **shall** be persisted before its triggering message is acknowledged. | Must | Implemented |
| `SYS-53` | Every service **shall** recover pending persisted work at startup. | Must | Implemented |
| `SYS-54` | A consumer **shall** stop accepting new messages on shutdown, finish or safely requeue in-flight work, then close its connections. | Must | Implemented |
| `SYS-55` | A failing external source **shall not** prevent other sources from completing. | Must | Implemented |
| `SYS-56` | Transient failures **shall** use bounded retry with backoff; unbounded or fixed-sleep retry **shall not** be used. | Must | Implemented |
| `SYS-57` | Replaying a dead-lettered message **shall** be an explicit operator action, never automatic. | Must | Implemented |

### 6.2 Security

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-58` | The system **shall** bind its services to the local environment only. | Must | Implemented |
| `SYS-59` | Outbound article fetching **shall** permit only the `http` and `https` schemes. | Must | Implemented |
| `SYS-60` | Outbound article fetching **shall** reject destinations resolving to loopback, private, link-local, multicast, reserved, or unspecified addresses, including cloud metadata endpoints. | Must | Implemented |
| `SYS-61` | Every redirect target **shall** be re-resolved and re-validated before it is followed. | Must | Implemented |
| `SYS-62` | Outbound fetches **shall** bound response size, timeout, redirect count, and accepted content types. | Must | Implemented |
| `SYS-63` | Article text **shall** be treated as untrusted data in LLM prompts and **shall** be structurally delimited. | Must | Implemented |
| `SYS-64` | Secrets **shall** be supplied as environment variables and **shall not** be committed to the repository. | Must | Implemented |
| `SYS-65` | Logs **shall not** contain secrets, full article bodies, or full prompts. | Must | Implemented |

### 6.3 Observability

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-66` | Every service **shall** expose `GET /health` reporting process liveness. | Must | Implemented |
| `SYS-67` | Every service **shall** expose `GET /ready` reporting the readiness of its dependencies. | Must | Implemented |
| `SYS-68` | Logs **shall** be emitted as structured JSON. | Must | Implemented |
| `SYS-69` | Every log entry **shall** include the correlation ID, service, operation, and status. | Must | Implemented |

### 6.4 Maintainability

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-70` | All code **shall** pass `ruff check` with no violations. | Must | Implemented |
| `SYS-71` | All code **shall** pass `mypy --strict` with no errors. | Must | Implemented |
| `SYS-72` | All services **shall** share one root virtual environment and one editable `shared` package. | Must | Implemented |

### 6.5 Governance

Content-licensing and disclosure obligations for the POC. These are proven by inspection, not by test.
Per-source obligations are in [SRS-02 §5.8](SRS-02-ingestion.md#58-source-governance).

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `SYS-73` | The system **shall** record, for every news source, its terms and whether full article bodies or raw HTML may be retained. | Must | Approved |
| `SYS-74` | The system **shall** store the minimum article text the POC requires. | Must | Approved |
| `SYS-75` | The system **shall** document a retention period for stored article text. | Must | Implemented |
| `SYS-76` | Any presentation of a prediction **shall** carry a financial-information disclaimer stating that the output is experimental and is not financial advice. | Must | Approved |
| `SYS-77` | The system **shall not** hold two opposing active predictions for the same asset when both derive from the same event set. | Must | Implemented |

Deletion automation, backup/restore automation, and long-term audit infrastructure are deferred until
the POC graduates — see 13.4.

## 7. How the system works

### 7.1 Routing topology

All messages flow through one durable topic exchange, `feed.events`. Producers publish by routing
key; consumers own a queue bound to the keys they need.

| Routing key | Producer | Consumer queue | Consumer |
|---|---|---|---|
| `article.ingested` | Ingestion | `cleansing.articles` | Cleansing |
| `event.detected` | Cleansing | `prediction.events` | Prediction |
| `prediction.made` | Prediction | `verification.predictions` | Verification |
| `prediction.made` | Prediction | `notification.predictions` | Notification |
| `prediction.made` | Prediction | `gateway.predictions.live` | Gateway (not built) |
| `price.requested` | Verification | `market-data.price-requests` | Market Data |
| `price.observed` | Market Data | `verification.prices` | Verification |
| `prediction.scored` | Verification | `credibility.scored` | Credibility |
| `prediction.scored` | Verification | `gateway.scored.live` | Gateway (not built) |

Two rules explain the whole topology:

1. **Verification is the sole producer** of `price.requested` and `prediction.scored`. Prediction
   never requests a price; Market Data never scores.
2. **Fan-out is by binding, not by sharing.** `prediction.made` reaches both Verification and the
   Gateway because two queues are bound to the same key — they never compete for one message.

### 7.2 Worked example: one prediction, end to end

This walkthrough follows a single piece of news through all six services.

**Step 1 — Ingestion (hourly)**

- The scheduler fires and polls each configured source.
- An RSS feed returns an article: *"Missile strikes hit Red Sea shipping lane."*
- The URL is canonicalised, the body fetched under SSRF controls, text normalised, content hashed.
- One row is inserted into `ingestion.articles` and one row into `ingestion.outbox`, in one
  transaction.
- The outbox row is published as `ArticleIngested` with routing key `article.ingested` and a fresh
  `correlation_id` that every later message will carry.

**Step 2 — Cleansing**

- Consumes the message from `cleansing.articles`.
- Computes a SimHash; if a near-duplicate exists within 48 hours the article is discarded here.
- Generates a 1024-dimension embedding.
- Extracts actor/action/object and maps the action to the taxonomy → `MILITARY_CONFLICT`.
- Resolves scope from registry keywords. "Missile" is an industry keyword, so the event fans out to
  every member of `WEAPON_INDUSTRY` across markets.
- Detects `polarity` (`OCCURRENCE`) and condition tags (`TRANSPORT_AFFECTED`).
- Dual-gate clustering: embedding similarity ≥ 0.80 **and** compatible event type. Both must pass to
  join an existing cluster; otherwise a new cluster opens.
- The cluster stays open for a 30-minute quiet period so later reports of the same event can join.
- When the quiet period elapses, the cluster becomes `READY`, one `EventDetected` is built and
  published via the outbox.

**Step 3 — Prediction**

- Consumes from `prediction.events`.
- The event joins a 15-minute event-time context window, one per affected asset. Distinct events stay
  distinct inside the context; they are not merged.
- After the window closes (plus a 5-minute grace), the graph is queried for that asset.
- Edges fire selectively: an unconditional edge always fires; a conditioned edge fires only when its
  condition is in the event's `context_tags`. A `RESOLUTION` polarity inverts the edge's sign.
- Each firing edge becomes a signed force: `weight × reliability`, where reliability is
  `alpha / (alpha + beta)`.
- Forces are summed. The net-to-total ratio gives direction and confidence; agreeing edges' average
  weight gives magnitude.
- Stance check: if the asset already has an unscored prediction with the same direction and
  magnitude, nothing is published. If different, a new independent prediction is created.
- `PredictionMade` is published with `decision_method = GRAPH_ONLY` and zero LLM calls, at
  `propagation_depth = 0` with an empty `propagation_chain`.
- After pass 0, a propagation pass queries `CORRELATES_WITH` edges for each directionally predicted
  asset — under condition `UPSTREAM_UP` or `UPSTREAM_DOWN`, matching that asset's own predicted
  direction — and runs `decide()` for the downstream targets, up to
  `PREDICTION_MAX_PROPAGATION_DEPTH` hops (default 3). A visited set per pipeline run prevents
  cycles. `decide()` itself is unchanged, and a net ratio inside the deadband produces no
  prediction at all. Each depth level collects all correlation edges reaching a target before
  deciding it, so two upstream assets converging on one downstream asset have their forces summed
  (and may cancel to no prediction). Summation is per level: a target decided at one depth is not
  revised by an edge arriving at the next.
- Each propagated prediction is published as its own `PredictionMade`, carrying
  `propagation_depth` = its hop count and the `propagation_chain` of fired edges that reached it.

**Step 4 — Verification (first half)**

- Consumes from `verification.predictions`.
- Persists an evaluation row.
- Resolves `baseline_session` as the last completed session at `decision_at`, with no look-ahead, and
  `settlement_session` as the next session, using that asset's own market calendar and timezone.
- Publishes one `PriceRequested` carrying **both** sessions.

**Step 5 — Market Data**

- Consumes from `market-data.price-requests`.
- Persists the request **before** acknowledging the message, so a crash cannot lose the work.
- Routes to the adapter named by the asset's `provider` field, translating the canonical ID to a
  provider symbol only inside the adapter.
- Fetches the baseline close immediately if available; polls hourly for the settlement close until
  the settlement session has completed.
- When both closes exist, publishes one `PriceObserved` containing both, each with full provenance:
  source, provider symbol, bar time, fetch time, price kind, adjustment flag, registry version.

**Step 6 — Verification (second half)**

- Consumes from `verification.prices`.
- Computes `actual_return = (settlement - baseline) / baseline`.
- Applies the deadband and magnitude thresholds from section 5.6.
- Publishes one immutable `PredictionScored` carrying the contributing edges, source IDs, and the
  `propagation_chain` forwarded from the prediction, so Credibility knows which edge to credit.

**Step 7 — Credibility**

- Consumes from `credibility.scored`.
- Inserts `prediction_id` into `credibility.processed_predictions` first. If the row already exists,
  the message is acknowledged and nothing is learned — this is what makes redelivery safe.
- When `propagation_chain` is non-empty the outcome came from a propagated prediction, so credit goes
  to the `CORRELATES_WITH` edge of the **last** hop in the chain, not to the originating `CAUSES`
  edges. Direct predictions use the `CAUSES` path below, unchanged.
- For each contributing edge: credit proportional to its `influence_weight`. If that edge's own
  direction matched the actual direction, credit is added to `alpha`; otherwise to `beta`. An edge
  that dissented and was right is rewarded even though the overall prediction was wrong.
- For each source: credit split equally, `1/n`.
- Writes an append-only history row with before/after values and a 95% confidence interval.

### 7.3 Why the LLM is barely used

The system was designed to be token-efficient, and a controlled experiment made it stricter still.

- Deterministic tools run first: SimHash for duplicates, BGE-m3 for similarity, spaCy for extraction,
  a version-controlled dictionary for taxonomy mapping, graph traversal for decisions.
- Cleansing calls the LLM only when deterministic extraction cannot produce a valid event, or when
  sources state conflicting facts.
- **Prediction makes no LLM calls at all.** The POC-6 experiment compared graph-only prediction
  against graph-plus-LLM arbitration over 30 reviewed conflict contexts. The LLM path produced 2
  paired corrections but 3 harms and lower overall accuracy, so the result was recorded as `STOP`.
  `LLM_ARBITRATED` remains defined in the contract as a deferred experimental path.

### 7.4 Idempotency: how duplicate work is prevented

RabbitMQ delivers at least once, so every stage has a durable guard.

| Stage | Guard | Effect of a duplicate |
|---|---|---|
| Ingestion | `canonical_url` unique in `ingestion.articles` | No second article row or message |
| Cleansing | `article_id` primary key on fingerprints; `cluster_id` unique on events | No second fingerprint, cluster, or event |
| Prediction | `idempotency_key` unique = `(asset_id, window_start, horizon, context_version)` | No second prediction identity |
| Prediction | composite PK on `context_events` | No duplicate context membership |
| Verification | `prediction_id` PK on evaluations; `request_id` unique | Existing evaluation and request reused |
| Verification | `prediction_id` PK on scores | Existing score reused, nothing republished |
| Market Data | `request_id` PK; `(asset_id, session, registry_version)` unique | Stored observation reused |
| Credibility | `prediction_id` PK on `processed_predictions` | Zero weight change |

### 7.5 Sessions, calendars, and time

- Every timestamp crossing a boundary is timezone-aware UTC.
- Each asset declares its own `timezone` and `session_complete_at` in the registry, so a Stockholm
  listing completes at 18:00 local while a New York listing completes at 17:00 local.
- Daylight saving comes from the IANA tz database via `zoneinfo`, never from hardcoded rules — on
  2026-03-10 New York is already `UTC-4` while Stockholm is still `UTC+1`.
- **Holidays are deliberately not modelled.** Any weekday is a session. A local holiday appears as a
  missing provider bar, so the price request stays pending and retries. The cost is that a request
  waits out its retry budget on a holiday instead of skipping to the next real session.

## 8. Interfaces

### 8.1 Message envelope

Every message carries these fields:

| Field | Type | Purpose |
|---|---|---|
| `message_id` | UUID | Unique delivery identity, used for idempotency |
| `correlation_id` | UUID | Business-flow identity, constant end to end |
| `causation_id` | UUID or null | The message that caused this one |
| `occurred_at` | UTC datetime | When the domain event happened |
| `schema_version` | string | Initial value `1.0` |

Conventions: business timestamps are timezone-aware UTC; enum values are uppercase snake case; asset
values are canonical asset IDs.

### 8.2 Message catalogue

| Message | Producer | Carries | Detail |
|---|---|---|---|
| `ArticleIngested` | Ingestion | One normalised article, no raw HTML | [SRS-02 §8](SRS-02-ingestion.md#8-interfaces) |
| `EventDetected` | Cleansing | One event with provenance, polarity, condition tags, affected assets | [SRS-03 §8](SRS-03-cleansing.md#8-interfaces) |
| `PredictionMade` | Prediction | One prediction with contributing edges, decision method, `propagation_depth` (`0` = direct), and `propagation_chain` (empty for direct) | [SRS-04 §8](SRS-04-prediction.md#8-interfaces) |
| `PriceRequested` | Verification | One dual-session price request | [SRS-06 §8](SRS-06-verification.md#8-interfaces) |
| `PriceObserved` | Market Data | Two immutable closes with full provenance | [SRS-05 §8](SRS-05-market-data.md#8-interfaces) |
| `PredictionScored` | Verification | One immutable outcome with both closes, plus the `propagation_chain` forwarded from the prediction | [SRS-06 §8](SRS-06-verification.md#8-interfaces) |

Full field definitions live in each component's specification and in
[src/shared/shared/schemas/messages.py](../src/shared/shared/schemas/messages.py), which is the
executable source of truth.

### 8.3 HTTP endpoints across the system

| Service | Endpoint | Purpose |
|---|---|---|
| All seven | `GET /health` | Process liveness |
| All seven | `GET /ready` | Dependency readiness |
| Market Data | `GET /prices/recent` | Read-only recent closes, used by Prediction to derive `RISK_PREMIUM_ELEVATED` |

`GET /prices/recent` is the only cross-service HTTP call in the system. Everything else is messaging.

### 8.4 Scheduled jobs across the system

| Service | Job | Default interval |
|---|---|---|
| Ingestion | Poll all sources | 3600 s |
| Ingestion | Retention cleanup | 86400 s |
| Cleansing | Close ready clusters and relay outbox | 60 s |
| Prediction | Close ready contexts and relay outbox | 60 s |
| Verification | Relay outbox | 30 s |
| Market Data | Re-drive open price requests | 3600 s |
| Credibility | Offline structure learner | 24 h |

## 9. Data design

### 9.1 PostgreSQL

One instance, one database `feed`, six schemas. Schemas and the `vector` extension are created by
[infra/postgres/01-init-database.sql](../infra/postgres/01-init-database.sql); each service creates
its own tables.

| Schema | Owner service | Holds |
|---|---|---|
| `ingestion` | Ingestion | Articles, outbox |
| `cleansing` | Cleansing | Fingerprints, embeddings, actions, clusters, events, outbox |
| `prediction` | Prediction | Contexts, context events, predictions, contributing edges, outbox |
| `market_data` | Market Data | Price requests, close observations, outbox |
| `verification` | Verification | Evaluations, price observations, scores, outbox |
| `credibility` | Credibility | Current weights, history, processed-prediction ledger |

### 9.2 Neo4j graph model

```text
(:CausalFactor {id})-[:CAUSES {condition, direction, weight, confidence,
                               alpha, beta, last_updated}]->(:Asset {id})

(:CausalFactor {id})-[:CAUSES {...}]->(:AssetGroup {id})    -- industry-level, inherited

(:Asset {id})-[:MEMBER_OF]->(:AssetGroup {id})

(:Asset {id})-[:CORRELATES_WITH {condition, direction, weight, confidence,
                                 alpha, beta, last_updated}]->(:Asset {id})
              -- condition is always set (UPSTREAM_UP or UPSTREAM_DOWN); no unconditional form
              -- fires during Prediction's propagation pass when the source asset was
              --   predicted directional in the same pipeline run
              -- alpha/beta learned by Credibility (online) and by the offline structure learner
```

Rules:

- A `CAUSES` edge without a `condition` property is unconditional and always fires.
- Each `(factor, target, condition)` triple is a distinct edge with its own weight and its own
  `alpha`/`beta`.
- An edge may target a single asset or a whole industry group.
- Every asset belongs to exactly one group, commodities included — `GOLD` is in `PRECIOUS_METALS`.
- An asset's own edge always **overrides** its group's edge; it does not add to it.
- When an asset has no edge of its own for a `(factor, condition)` pair, it inherits the group's, so
  a newly listed company can predict before it has any company-specific evidence.
- A `CORRELATES_WITH` edge always carries a `condition`, so each `(source, target, condition)` triple
  is a distinct edge with its own weight and its own `alpha`/`beta`. The invariant is enforced by the
  `MERGE` pattern in the seed and by application code, **not** by the database: a relationship
  property existence constraint is Neo4j Enterprise only and this stack runs `neo4j:5.20-community`.
  A lookup index on the relationship property `condition` is created, for the propagation query.
- `CORRELATES_WITH` edges link assets only; they are never inherited from a group.

Edge business key, used in `ContributingEdge.edge_id`:

| Form | Meaning |
|---|---|
| `FACTOR->TARGET` | Unconditional edge |
| `FACTOR\|CONDITION->TARGET` | Conditioned edge |

`TARGET` is an asset ID, or a group ID when the edge was inherited. Credibility parses both forms, so
an inherited edge updates the industry prior that actually fired rather than creating a per-asset
edge that was never seeded.

### 9.3 Seed data

Applied from [infra/neo4j/init/](../infra/neo4j/init/) in filename order:

| File | Content |
|---|---|
| `01-constraints-indexes.cypher` | Uniqueness constraints and indexes |
| `02-seed-assets.cypher` | Asset and group nodes, generated from the registry |
| `03-seed-causal-factors.cypher` | One node per event taxonomy type |
| `04-seed-causal-edges.cypher` | Expert-assigned unconditional edges |
| `05-seed-conditioned-edges.cypher` | Condition-qualified edges |
| `06-seed-group-edges.cypher` | Industry-level priors, inherited by members |
| `07-seed-new-event-type-edges.cypher` | Edges for later taxonomy additions |
| `08-seed-correlation-edges.cypher` | `CORRELATES_WITH` asset-to-asset edges and the `condition` lookup index |

All seeded edges start at `alpha = 1.0`, `beta = 1.0`. Regenerate the asset seed after any registry
edit with `python scripts/generate-asset-seed.py`.

## 10. Configuration

Shared variables, set in `infra/.env` from
[infra/.env.example](../infra/.env.example). Service-specific keys are in each SRS section 10.

| Variable | Default | Effect |
|---|---|---|
| `POSTGRES_USER` | `feed_user` | Database role owning all six schemas |
| `POSTGRES_PASSWORD` | — | Required; no default in production use |
| `POSTGRES_DB` | `feed` | The single database name |
| `DATABASE_URL` | `postgresql://feed_user:...@localhost:5432/feed` | Connection string used by every service |
| `NEO4J_AUTH` | `neo4j/feedpassword` | Neo4j credentials, `user/password` form |
| `NEO4J_PASSWORD` | `feedpassword` | Used by the seed container |
| `NEO4J_URI` | `bolt://localhost:7687` | Graph connection |
| `RABBITMQ_DEFAULT_USER` | `feed_user` | Broker user |
| `RABBITMQ_DEFAULT_PASS` | — | Broker password |
| `RABBITMQ_URL` | `amqp://feed_user:...@localhost:5672/` | Broker connection |
| `ASSET_REGISTRY_PATH` | packaged `assets.json` | Overrides the asset registry file |
| `LLM_PROVIDER` | `openai` | Selects the gateway adapter; `openai` means any OpenAI-compatible endpoint |
| `LLM_MODEL` | — | Provider model ID |
| `LLM_BASE_URL` | empty | Custom endpoint; empty uses the provider default |
| `LLM_API_KEY` | empty | Secret; empty disables LLM-assisted paths |
| `LLM_MAX_INPUT_TOKENS` | `6000` | Input budget, enforced before the call is made |
| `LLM_MAX_OUTPUT_TOKENS` | `512` | Output cap |
| `LLM_TIMEOUT_SECONDS` | `30` | Per-call timeout |
| `LOG_LEVEL` | `INFO` | Structured log threshold |

## 11. Verification

System-level behaviours and the tests that prove them.

| Requirement | Method | Evidence |
|---|---|---|
| `SYS-2` | Test | [test_clustering.py](../src/services/cleansing/tests/test_clustering.py) — distinct event types stay separate above the similarity threshold |
| `SYS-3` | Test | [test_context.py](../src/services/prediction/tests/test_context.py) — multiple events form one versioned context |
| `SYS-4` | Test | [test_decision.py](../src/services/prediction/tests/test_decision.py) — contributing edges recorded on the decision |
| `SYS-5` | Test | [test_scoring.py](../src/services/verification/tests/test_scoring.py) — close-to-close scoring |
| `SYS-6` | Test | [test_pipeline.py](../src/services/credibility/tests/test_pipeline.py) — replay changes no counter |
| `SYS-9`, `SYS-10` | Test | [test_asset_id.py](../src/shared/tests/test_asset_id.py) — unknown IDs rejected at the boundary |
| `SYS-11` | Test | [test_taxonomy.py](../src/services/cleansing/tests/test_taxonomy.py) — taxonomy mapping and `OTHER` fallback |
| `SYS-12`, `SYS-13`, `SYS-14` | Test | [test_asset_loader.py](../src/shared/tests/test_asset_loader.py) — data-driven registry, malformed file refuses to load |
| `SYS-15`…`SYS-18` | Inspection | [01-init-database.sql](../infra/postgres/01-init-database.sql) and each service's `db.py` |
| `SYS-20`…`SYS-22` | Test | [test_infrastructure.py](../src/shared/tests/test_infrastructure.py) — exchange, queues, bindings, DLQs |
| `SYS-23`, `SYS-24` | Test | [test_schemas.py](../src/shared/tests/test_schemas.py) — envelope fields and round trip |
| `SYS-25` | Test | Each service's outbox tests, e.g. [test_pipeline.py](../src/services/verification/tests/test_pipeline.py) |
| `SYS-26` | Test | Idempotency tests per service, see 7.4 |
| `SYS-29`…`SYS-31` | Test | [test_pipeline.py](../src/services/prediction/tests/test_pipeline.py) — graph-only decisions make zero LLM calls |
| `SYS-32`…`SYS-38` | Test | [test_llm_gateway.py](../src/shared/tests/test_llm_gateway.py) — provider selection, budgets, cache, single retry |
| `SYS-39`…`SYS-43` | Test | [test_scoring.py](../src/services/verification/tests/test_scoring.py) — return, deadband, magnitude, correctness |
| `SYS-44`, `SYS-45` | Test | [test_storage.py](../src/services/market-data/tests/test_storage.py) — provenance and price kind preserved |
| `SYS-46`…`SYS-50` | Test | [test_updater.py](../src/services/credibility/tests/test_updater.py) — priors, separated targets, per-edge credit |
| `SYS-52`, `SYS-53` | Test | [test_handler.py](../src/services/market-data/tests/test_handler.py) — persist before ack, rehydrate at startup |
| `SYS-55`, `SYS-56` | Test | [test_resilience.py](../src/services/ingestion/tests/test_resilience.py) — circuit breaker and bounded backoff |
| `SYS-59`…`SYS-62` | Test | [test_fetcher.py](../src/services/ingestion/tests/test_fetcher.py) — SSRF, redirect revalidation, bounds |
| `SYS-66`…`SYS-69` | Test | [test_logging.py](../src/shared/tests/test_logging.py) and each service's integration test |
| `SYS-70`, `SYS-71` | Demonstration | `ruff check` and `mypy --strict` in CI |
| `SYS-75` | Inspection | [SRS-02 §10.5](SRS-02-ingestion.md#10-configuration) — `ARTICLE_RETENTION_DAYS` default 30, `OUTBOX_RETENTION_DAYS` default 7 |
| `SYS-73`, `SYS-74`, `SYS-76` | Inspection | **Not yet enforced.** Source terms are not modelled on a source record, and no Gateway or Dashboard exists to carry the disclaimer — `Approved`, not `Implemented` |
| `SYS-77` | Test | [SRS-04 PRD-61](SRS-04-prediction.md#5-functional-requirements) — `prediction/tests/test_pipeline_e12.py::test_a_directly_decided_asset_is_not_reached_by_propagation`. Violated in production until 2026-08-14: one macro event reached both ends of an anti-correlated asset pair and each propagated a contradiction onto the other, so NEM_NYSE held 16 UP and 13 DOWN predictions from one day's news |

## 12. Failure handling

| Failure | System behaviour | Recovery |
|---|---|---|
| One news source fails | Other sources continue; the failing source's circuit opens after repeated failures | Circuit closes after its reset timeout |
| PostgreSQL unavailable | Readiness fails; consumers stop taking new work | Automatic on reconnection |
| RabbitMQ unavailable | Publication is retried from the outbox; consumption pauses | Outbox relay drains on reconnection |
| Neo4j unavailable | Prediction readiness fails; Credibility graph updates stay durable | Applied after recovery |
| LLM unavailable | Ambiguous clusters marked `ERROR_RETRYABLE`; no event is fabricated | Retried when the gateway returns |
| Price provider unavailable | The request stays pending with bounded backoff | Hourly re-drive |
| Price not yet published | The request stays pending — this is normal, not an error | Next scheduled attempt |
| Malformed message | Dead-lettered with failure metadata | Explicit operator replay |
| Unsupported schema version | Dead-lettered with `unsupported_schema_version` | Deploy a compatible consumer, then replay |
| Crash mid-publish | The outbox row stays `PENDING` | Relayed at startup |

## 13. Assumptions, dependencies, and known limitations

### 13.1 Assumptions

- The system runs locally under Docker Compose, with no external network exposure.
- News sources continue to serve the formats their adapters expect.
- Provider price endpoints continue to serve daily bars for the registry's symbols.
- Only recent articles need de-duplicating, so time-based retention is safe: feeds surface only
  recent items, and an article older than the retention window can never be re-ingested.

### 13.2 Accepted design decisions

| Decision | Reason |
|---|---|
| Prediction makes no LLM calls | The POC-6 controlled rerun (2026-07-13) recorded `STOP`: over 30 reviewed conflict contexts, graph-plus-LLM produced 2 paired corrections but 3 harms and lower accuracy than graph-only |
| Holidays are not modelled | Any weekday is a session; a holiday surfaces as a missing bar and the request retries |
| Raw, unadjusted closes only | A back-adjusted series silently changes historical values between fetches, which would break immutable observations |
| The asset registry loads once at startup | Reloading mid-flight would let one message validate against a different asset set than the one that produced it |
| Deterministic default backends for embedding and NLP | The walking skeleton and its tests run without downloading multi-gigabyte models; the real models load lazily via the `ml` extra |

### 13.3 Known limitations

| Limitation | Consequence |
|---|---|
| Industry news fans out to every group member | One headline over a 4-member group produces 4 predictions whose outcomes are correlated but scored as independent, inflating apparent confidence. Not yet bounded. |
| Group edges accumulate evidence faster | Several listings in one group can credit the same inherited edge from one industry event |
| Yahoo's endpoint is undocumented | It requires a browser User-Agent and its behaviour may change without notice; US assets stay on biquote so Yahoo flakiness cannot regress existing scoring |
| A market holiday consumes retry budget | The request waits rather than skipping to the next real session |
| No Gateway or Dashboard | Results are read from the database or the Market Data read endpoint |
| Calibration reports not yet produced | Confidence is recorded, but Brier score and reliability reports need more history |
| `mypy` cannot prove exhaustive asset coverage | Accepted trade-off of a data-driven registry over a closed enum |

### 13.4 Deferred work

- Formal FR/NFR/BR traceability (superseded by the requirement IDs in these documents).
- Full monitoring, dashboards, and distributed tracing.
- Load and soak testing.
- Public API authentication and rate limiting.
- Automated backup, restore, and deletion mechanics.
- Prediction-time LLM arbitration, pending a new approved controlled hypothesis.

## 14. How to update this document

Follow the rules in [README.md](README.md#how-to-update-these-documents).

Update this document, not just a component SRS, when a change:

- adds or removes a service, queue, routing key, or exchange binding;
- changes the message envelope or a cross-service contract;
- changes a scoring or learning rule in sections 5.6 or 5.7;
- changes schema ownership or the graph model;
- changes a shared configuration variable;
- adds a system-wide limitation or accepted trade-off.

Keep the component tables in 4.2 and the topology table in 7.1 in step with reality — they are the
first things a new reader uses to orient.

## 15. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| `2026-08-05` | `1.0.0` | Initial system specification, written from the implemented E01–E07 code | E01–E07 complete; replaces the epic/task backlog structure |
| `2026-08-05` | `1.1.0` | Added Notification Service (Approved): section 4.2 component row, section 7.1 routing topology binding, section 8.3 endpoint count, scope updated | SRS-10 added |
| `2026-08-06` | `1.2.0` | Added section 6.5 governance (`SYS-73`…`SYS-76`, mostly `Approved`) and its verification rows | Merged from `docs/requirements/agreed-system-requirements.md` "Governance for the POC"; the disclaimer and source-terms obligations had no requirement ID anywhere |
| `2026-08-12` | `1.3.0` | E10 cross-asset propagation: section 3 definitions for `CORRELATES_WITH`, propagation pass, visited set, `propagation_depth`, `propagation_chain`, `PropagationHop`; section 7.2 steps 3, 6 and 7 narrative; section 8.2 `PredictionMade` and `PredictionScored` rows; section 9.2 edge type and rules; section 9.3 seed file | ADR-008; E10 implemented and verified live |
