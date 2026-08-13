# SRS-04 — Prediction Service

**Document ID:** SRS-04  
**Status:** Implemented  
**Priority:** Must  
**Component prefix:** PRD  
**Related system requirements:** SYS-34 – SYS-47  
**Epic:** E04 (Prediction Service)

---

## Table of Contents

1. [Document Control](#1-document-control)
2. [Purpose and Scope](#2-purpose-and-scope)
3. [Definitions](#3-definitions)
4. [System Context](#4-system-context)
5. [Functional Requirements](#5-functional-requirements)
6. [Non-Functional Requirements](#6-non-functional-requirements)
7. [How It Works](#7-how-it-works)
8. [Interfaces](#8-interfaces)
9. [Data Design](#9-data-design)
10. [Configuration](#10-configuration)
11. [Verification](#11-verification)
12. [Failure Handling](#12-failure-handling)
13. [Assumptions and Limitations](#13-assumptions-and-limitations)
14. [How to Update This Document](#14-how-to-update-this-document)
15. [Change History](#15-change-history)

---

## 1. Document Control

| Field | Value |
|---|---|
| Author | Feed Analyzer project |
| Version | `1.1.0` |
| Created | 2026-08-05 |
| Last updated | 2026-08-12 |
| Last verified against code | `2026-08-12` |
| Replaces | `docs/functional-documents/prediction-service-functional-document.md` (deleted 2026-08-06) |
| Source code | `src/services/prediction/` |
| Config class | `prediction.config.PredictionSettings` |
| DB schema | `prediction` (owned by this service) |

---

## 2. Purpose and Scope

### 2.1 What this service does

The Prediction Service is the third stage in the pipeline. It receives structured `EventDetected` messages from the Cleansing Service and produces `PredictionMade` messages containing a directional view on the next trading session's close.

Specific responsibilities:

- **Context aggregation** — for each incoming event, create or update a per-asset 15-minute tumbling context window
- **Graph traversal** — query Neo4j for CAUSES edges that fire given the aggregated event types and conditions
- **Force summation** — combine firing edge directions and reliability-weighted strengths into one net direction, confidence, and magnitude (graph-only policy, M1)
- **Stance management** — decide whether to emit a new prediction, skip a duplicate, or supersede the previous stance on a closed market
- **Prediction emission** — persist the `PredictionMade` message and outbox row atomically; relay to the broker on a scheduled sweep
- **Cross-asset propagation** — for each asset that received a directional prediction, query `CORRELATES_WITH` edges active under the matching condition (`UPSTREAM_UP` / `UPSTREAM_DOWN`) and run `decide()` for each downstream target, up to `max_propagation_depth` hops. Each depth level collects all inbound correlation edges per target before deciding, so converging edges have their forces summed by the same rule used for `CAUSES` edges. A per-pipeline-run visited set (seeded with the direct asset) prevents re-predicting an asset that was already decided in this run

### 2.2 What it does not do

- Does not call any LLM at prediction time (M1 graph-only policy; POC-6 STOP)
- Does not fetch raw news or detect events (Ingestion and Cleansing handle that)
- Does not fetch market prices directly; uses the Market Data service HTTP API as a read-only source
- Does not score predictions (Verification Service)
- Does not update edge weights (Credibility Service)

---

## 3. Definitions

| Term | Meaning |
|---|---|
| Context window | A 15-minute tumbling window aligned to UTC midnight; all events for one asset within the window form one context |
| Context version | An integer that increments when a late event extends an already-closed context (see 7.3) |
| FiringEdge | A CAUSES relationship in Neo4j that is active given the query's event type and conditions |
| Force | Signed strength of one firing edge: `weight × reliability`, where reliability = `alpha / (alpha + beta)` |
| Consensus | `abs(sum(signed forces)) / sum(absolute forces)` for all directional edges; how much the firing edges agree. 1.0 when nothing opposes |
| Evidence mass | `total / (total + confidence_evidence_halfpoint)` where `total = sum(absolute forces)`; how much evidence there is at all |
| Confidence | `consensus × evidence mass`. Consensus alone is self-normalising, so with a single firing edge the strength cancels and every such prediction scored 1.00 regardless of how weak the edge was |
| Deadband | If `confidence < deadband (0.15)` the direction is NEUTRAL. Applied to the evidence-weighted confidence, not to consensus: the old ratio form was unreachable for a single firing edge, so NEUTRAL was never emitted |
| Magnitude | SMALL / MEDIUM / LARGE based on average expert weight of the agreeing-direction edges |
| M1 / GRAPH_ONLY | The decision method used exclusively in this POC: graph traversal + force summation, zero LLM calls |
| POC-6 STOP | Experiment result confirming LLM arbitration does not improve accuracy; M1 is the sole policy |
| Scope-B price gate | Suppresses a RESOLUTION-driven DOWN force when the asset's current price is not elevated |
| RISK_PREMIUM_ELEVATED | Condition code injected at query time (not by Cleansing) when the price gate detects elevation |
| Stance | The most recently emitted, non-withdrawn `PredictionMade` for an asset |
| Supersede | Withdraw the prior stance and replace it with a new one (market-closed path only) |
| Horizon | `ONE_TRADING_DAY` — the only prediction horizon produced |
| Idempotency key | `"{asset_id}|{window_start}|{horizon}|{context_version}"` — unique constraint prevents double-emit; a propagated prediction appends a `prop{depth}` suffix segment so it cannot collide with the direct prediction's key (see PRD-56) |
| Inherited edge | A group-level CAUSES edge applied to a company that has no company-specific edge for the same factor+condition |
| CorrelationEdge | An `(:Asset)-[:CORRELATES_WITH {condition}]->(:Asset)` relationship in Neo4j; unlike CAUSES it has no unconditional form (`condition` is always set) |
| Propagation pass | One `decide()` sweep over the assets reachable via `CORRELATES_WITH` edges from the assets decided in the previous pass; pass 0 is the direct CAUSES prediction |
| Propagation depth | Integer on `PredictionMade`: 0 for a direct prediction, 1+ for a propagated one; counts the number of `CORRELATES_WITH` hops from the direct asset |
| Visited set | Per-pipeline-run set of `AssetId`s already decided, seeded with the direct asset before propagation begins; prevents cycles and duplicate predictions |
| UPSTREAM_UP / UPSTREAM_DOWN | Condition codes that gate a `CORRELATES_WITH` edge on the source asset's predicted direction (UP → `UPSTREAM_UP`, otherwise `UPSTREAM_DOWN`) |
| PropagationHop | Value model recording one fired `CORRELATES_WITH` edge: `(source_asset_id, target_asset_id, condition, direction, edge_weight)` |
| PREDICTION_MAX_PROPAGATION_DEPTH | Config ceiling on the hop count (settings field `max_propagation_depth`, default 3, range 1–10). The `PREDICTION_` prefix is required — see [§10](#10-configuration) |

---

## 4. System Context

```
[Cleansing Service]
       |
       | EventDetected (routing key: event.detected)
       | Queue: prediction.events
       v
[Prediction Service]
  - build/update context window per asset
  - query Neo4j for firing edges (+ conditions)
  - force summation → direction / confidence / magnitude
  - propagation passes over CORRELATES_WITH edges (depth-capped, visited-guarded)
  - stance management (market-open vs closed)
       |
       | PredictionMade (routing key: prediction.made)
       v
[Verification Service]   [Market Data Service – price request triggered]

[Market Data Service] <── HTTP GET /prices/recent/{asset_id}   (Scope-B price gate)
[Neo4j]              <── Cypher query (firing edges)
[Neo4j]              <── Cypher query (correlation edges, per propagation pass)
```

- Consumes from queue: `prediction.events`
- Publishes to exchange: `feed.events` with routing key `prediction.made`
- Calls: Market Data Service HTTP API for recent close prices (Scope-B gate)
- Calls: Neo4j graph for CAUSES edges (direct) and CORRELATES_WITH edges (propagation)
- Database schema: `prediction` (five application tables)
- No LLM calls at any point

---

## 5. Functional Requirements

### 5.1 Message consumption

| ID | Requirement | Status |
|---|---|---|
| PRD-1 | The service shall consume `EventDetected` messages from the `prediction.events` queue on the durable `feed.events` exchange | Implemented |
| PRD-2 | Each event shall be assigned to the context windows of every asset listed in `affected_asset_ids`; an event with an empty `affected_asset_ids` shall be acknowledged and discarded with a log entry | Implemented |
| PRD-3 | Adding the same `event_id` to the same context a second time (at-least-once redelivery) shall be a no-op; the composite PK `(context_id, event_id)` enforces this | Implemented |

### 5.2 Context windowing

| ID | Requirement | Status |
|---|---|---|
| PRD-4 | The service shall map each event to a 15-minute tumbling window aligned to UTC midnight using `window_bounds(event.first_seen_at, window_minutes)` | Implemented |
| PRD-5 | Each `(asset_id, window_start, context_version)` triple shall be unique; the service shall use `ON CONFLICT DO NOTHING` semantics when creating a context row | Implemented |
| PRD-6 | The service shall store the event's `polarity` and `context_tags` on the `context_events` row so they are available at decision time without re-fetching the original message | Implemented |
| PRD-7 | Context version starts at 1; a new version is created when a late event (after the window closed) needs to be incorporated into a new prediction | Implemented |

### 5.3 Context closing

| ID | Requirement | Status |
|---|---|---|
| PRD-8 | The service shall run a scheduled job every `PREDICTION_CLOSE_INTERVAL_SECONDS` (default 60 s) that claims all OPEN contexts where `now >= window_end + close_grace_minutes` | Implemented |
| PRD-9 | `close_grace_minutes` (default 5) allows slightly late events to arrive before the window is claimed | Implemented |
| PRD-10 | The scheduled job shall load all `context_events` for each claimed context before invoking the decision policy | Implemented |

### 5.4 Graph traversal

| ID | Requirement | Status |
|---|---|---|
| PRD-11 | The service shall query Neo4j for CAUSES edges for each distinct event type in the context, filtered to the target asset | Implemented |
| PRD-12 | Conditioned edges shall fire only when their `condition` code appears in the active condition set for that event type | Implemented |
| PRD-13 | The active condition set for an event type is the union of `context_tags` across all context events of that type | Implemented |
| PRD-14 | When the asset's price is elevated (Scope-B gate), `RISK_PREMIUM_ELEVATED` shall be added to EVERY event type's condition set for that asset | Implemented |
| PRD-15 | When a company has no direct CAUSES edge for a (factor, condition) pair, the service shall inherit the industry group's edge; a company-specific edge always wins over the group edge | Implemented |
| PRD-16 | Each distinct event type shall be queried once; a factor appearing in multiple context events is NOT double-counted | Implemented |

### 5.5 Force summation (GRAPH_ONLY, M1)

| ID | Requirement | Status |
|---|---|---|
| PRD-17 | The service shall compute the reliability of each firing edge as `alpha / (alpha + beta)` where `alpha` and `beta` are the Beta-Bernoulli counts stored on the edge | Implemented |
| PRD-18 | The force of each edge is `weight × reliability`; force is signed (positive for UP, negative for DOWN) | Implemented |
| PRD-19 | A `RESOLUTION` polarity event shall negate the direction of its factor's edges: UP becomes DOWN, DOWN becomes UP | Implemented |
| PRD-20 | A factor is treated as RESOLUTION only when ALL context events of that type have `polarity = RESOLUTION`; any single OCCURRENCE keeps the stored sign | Implemented |
| PRD-21 | The Scope-B price gate: a RESOLUTION-driven DOWN force is counted only when the asset's latest close is elevated; if the price is not elevated, that edge's contribution is dropped | Implemented |
| PRD-22 | If dropping Scope-B edges leaves no directional edge, the decision function shall return `None` and no prediction is emitted | Implemented |
| PRD-23 | `net_ratio = sum(signed forces) / sum(absolute forces)` over all directional edges | Implemented |
| PRD-24 | If `confidence < decision_deadband` (default 0.15), direction is NEUTRAL. A NEUTRAL prediction **is** emitted: roughly a quarter of real outcomes are flat, and Verification scores a correct NEUTRAL call (SRS-06 §7) | Implemented |
| PRD-25 | `confidence = min(1.0, consensus × evidence_mass)` rounded to 4 decimal places, where `consensus = abs(net)/total` and `evidence_mass = total/(total + confidence_evidence_halfpoint)` (default half-point 0.5) | Implemented |
| PRD-26 | Magnitude is derived from the average expert `weight` of the agreeing-direction edges: `< 0.40` → SMALL; `< 0.70` → MEDIUM; else LARGE | Implemented |
| PRD-27 | The decision method shall always be `GRAPH_ONLY` | Implemented |
| PRD-28 | All firing edges (including opposing and neutral ones) shall be recorded in `contributing_edges` for explainability, using their polarity-adjusted direction | Implemented |
| PRD-29 | The rationale string shall include asset, direction, magnitude, confidence, and a summary of contributing edges; max 2000 characters | Implemented |

### 5.6 Stance management

| ID | Requirement | Status |
|---|---|---|
| PRD-30 | The service shall check whether the market is open before emitting a prediction: `is_trading_day(local_date_in(now, timezone)) AND is_price_available(asset_id)` | Implemented |
| PRD-31 | On a trading day (market open): if the latest active prediction has the same **direction**, the context shall be marked PREDICTED and no new prediction emitted. Magnitude is deliberately not part of this test — a change of degree is not a change of stance | Implemented |
| PRD-32 | On a trading day: if the new decision differs in **direction** from the active prediction, a new independent prediction shall be emitted with `supersedes_prediction_id = None`, subject to the daily limit in PRD-32a | Implemented |
| PRD-32a | On a trading day an asset shall hold at most `max_daily_predictions_per_asset` (default 2) non-withdrawn predictions. Once reached, further decisions are skipped with `prediction_daily_limit_reached` and the context marked PREDICTED. Combined with PRD-31/PRD-32 this allows one initial stance plus one reversal per day | Implemented |
| PRD-32b | The daily count shall be taken per asset in the **asset's own timezone**, so a Stockholm and a New York listing each roll over at their own market's midnight (`decision_at` is stored in UTC) | Implemented |
| PRD-32c | Withdrawn predictions shall not count toward the daily limit: they were superseded and never stood as the asset's stance | Implemented |
| PRD-33 | On a non-trading day (market closed): if an active prediction exists, it shall be superseded and withdrawn; the new prediction shall carry `supersedes_prediction_id` | Implemented |
| PRD-34 | On a non-trading day with no prior active prediction, the new prediction is emitted with `supersedes_prediction_id = None` | Implemented |
| PRD-35 | A withdrawn prediction shall have `status = 'WITHDRAWN'` in the `predictions` table; this happens atomically with the insertion of the new prediction in the same transaction | Implemented |
| PRD-36 | An unknown asset (not in the registry) shall be treated as market-closed (the conservative collapse path) with a warning log | Implemented |

### 5.7 Prediction storage and outbox

| ID | Requirement | Status |
|---|---|---|
| PRD-37 | The `PredictionMade` row, `contributing_edges` rows, and outbox row shall be written in a single database transaction | Implemented |
| PRD-38 | The idempotency key `"{asset_id}|{window_start}|{horizon}|{context_version}"` shall have a UNIQUE constraint; a duplicate key conflict means the prediction was already stored and the context is marked PREDICTED | Implemented |
| PRD-39 | The service shall sweep `prediction.outbox_events` for PENDING rows and publish them to `feed.events` with routing key `prediction.made` on each scheduled job run | Implemented |
| PRD-40 | A per-row publish failure shall increment `attempts` and write `last_error` without blocking other rows | Implemented |

### 5.8 Cross-asset propagation

| ID | Requirement | Status |
|---|---|---|
| PRD-48 | After the direct (pass 0) prediction is stored, the service shall run a propagation loop for every source asset whose decision direction is UP or DOWN; the loop continues while the previous pass produced at least one directional target AND `depth < max_propagation_depth`. The context is set to PREDICTED only after the loop finishes | Implemented |
| PRD-49 | For each source asset the propagation condition shall be derived from its predicted direction: `UPSTREAM_UP` when the direction is UP, otherwise `UPSTREAM_DOWN`; the service shall then query `get_correlation_edges(source_asset_id, condition)` for the active `CORRELATES_WITH` edges | Implemented |
| PRD-50 | The service shall maintain a `visited` set of `AssetId`s per pipeline run, seeded with the direct context's asset before propagation begins. A correlation edge whose target is already in `visited` shall be skipped, and every newly reached target shall be added to `visited` — this is the sole cycle guard | Implemented |
| PRD-51 | Each fired correlation edge shall be presented to the **unchanged** `decide()` function as a synthesised `FiringEdge` with `factor_id = None`, carrying the correlation edge's `direction`, `weight`, `confidence`, `alpha`, `beta`, and the derived `condition` | Implemented |
| PRD-52 | A propagated decision that is `None` or NEUTRAL shall emit no prediction, and that target shall not seed the next pass | Implemented |
| PRD-53 | Each propagated `PredictionMade` shall carry `propagation_depth >= 1` and a `propagation_chain` listing every `PropagationHop` from the direct asset to that target, in hop order | Implemented |
| PRD-54 | A direct `PredictionMade` shall carry `propagation_depth = 0` and an empty `propagation_chain` (backward-compatible defaults on the message schema) | Implemented |
| PRD-55 | A propagated prediction shall reuse the **source** context: `context_id`, `context_version`, `window_start`, `window_end`, and `event_ids` are those of the direct context; only `asset_id` is re-targeted | Implemented |
| PRD-56 | The idempotency key of a propagated prediction shall append `"\|prop{depth}"` to the standard key so it can never collide with the direct prediction's key for the same window and version | Implemented |
| PRD-57 | Propagated predictions shall pass through the same stance management as direct ones (market-open duplicate skip, market-closed supersede-and-withdraw) | Implemented |
| PRD-58 | A `GraphError` raised while querying correlation edges for one source asset shall be caught, logged as `propagation_graph_error` with the source asset and depth, and that source skipped; the remaining sources, the direct prediction, and the rest of the close sweep are unaffected and the context is **not** marked `ERROR_RETRYABLE` | Implemented |
| PRD-59 | `max_propagation_depth` shall be configurable via `PREDICTION_MAX_PROPAGATION_DEPTH`, default 3, and validated to the inclusive range 1–10 | Implemented |

### 5.9 Health and readiness

| ID | Requirement | Status |
|---|---|---|
| PRD-41 | The service shall expose `GET /health` returning `{"status": "ok"}` | Implemented |
| PRD-42 | The service shall expose `GET /ready` returning 200 only when the database pool, RabbitMQ consumer, and Neo4j driver are all healthy | Implemented |

---

## 6. Non-Functional Requirements

| ID | Requirement | Status |
|---|---|---|
| PRD-43 | Secrets (`DATABASE_URL`, `RABBITMQ_URL`, Neo4j password) shall be environment variables; none committed | Implemented |
| PRD-44 | No LLM call shall be made during prediction (POC-6 STOP) | Implemented |
| PRD-45 | The service shall bind to the local environment only | Implemented |
| PRD-46 | Graph queries shall be scoped to the target asset(s) to avoid full-graph scans | Implemented |
| PRD-47 | The scheduled close job shall not crash when a single context fails; a GraphError shall mark the context `ERROR_RETRYABLE` and processing continues with the next context | Implemented |

---

## 7. How It Works

### 7.1 Per-event pipeline (process_event)

This runs once per `EventDetected` message from the `prediction.events` queue.

**Step 1 — Check affected assets**
- If `event.affected_asset_ids` is empty: log `event_no_assets`, ack, return

**Step 2 — Compute context window**
- Call `window_bounds(event.first_seen_at, context_window_minutes)`
- Returns `(window_start, window_end)` — a UTC-aligned 15-minute bucket

**Step 3 — Assign event to each asset**
- For each `asset_id` in `event.affected_asset_ids`:
  - Upsert a context row for `(asset_id, window_start, context_version=1)` if it doesn't exist
  - Insert `(context_id, event_id, event_type, first_seen_at, polarity, context_tags)` with `ON CONFLICT DO NOTHING`

**Step 4 — Log**
- Log `event_contextualized` with event_id, event_type, polarity, context_tags, list of asset_ids

### 7.2 Context window alignment algorithm

```python
def window_bounds(event_time, window_minutes=15):
    day_start = event_time.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = (event_time - day_start).total_seconds() / 60.0
    bucket = int(elapsed_minutes // window_minutes)
    window_start = day_start + timedelta(minutes=bucket * window_minutes)
    window_end   = window_start + timedelta(minutes=window_minutes)
    return window_start, window_end
```

**Example:**
- `event_time = 2024-03-15T10:47:00Z`
- `elapsed_minutes = 647`
- `bucket = 647 // 15 = 43`
- `window_start = 00:00 + 43 × 15 min = 10:45:00Z`
- `window_end = 11:00:00Z`

Multiple events for the same asset between 10:45 and 11:00 all land in the same context.

### 7.3 Context close sweep

**Triggered by:** the scheduled job (`close_interval_seconds = 60 s`)

```
now = UTC timestamp

claimed = SELECT contexts WHERE state = 'OPEN'
                           AND now >= window_end + close_grace_minutes
          UPDATE state = 'CLAIMED' (atomic)

for each context record:
  events = load_context_events(context_id)
  elevated = price_reader.is_elevated(asset_id)   ← HTTP call to Market Data

  edges = query_firing_edges(events, elevated)     ← see 7.4

  decision = decide(asset_id, edges, ...)          ← see 7.5

  if decision is None:
    set context state = PREDICTED
    log no_prediction
    continue

  # Pass 0 — direct prediction. The visited set is seeded with this asset so a
  # correlation back-edge can never re-predict it in this run.
  visited = {asset_id}
  store_prediction(record, decision, now, events)   ← see below; propagation_depth = 0

  # Passes 1+ — cross-asset propagation (see 7.10)
  if decision.direction in (UP, DOWN):
    run_propagation(record, {asset_id: decision.direction}, visited, now, events)

  mark context state = PREDICTED
```

`store_prediction(record, decision, now, events, propagation_depth=0, propagation_chain=[])` is
shared by the direct and propagated paths and holds the stance management:

```
active = latest_active_prediction(record.asset_id)
market_open = is_market_open(record.asset_id, now)      ← see 7.6

if market_open:
  if active and active.direction == decision.direction and active.magnitude == decision.magnitude:
    log prediction_unchanged
    return False                                       ← no prediction stored
  supersedes = None
  withdraw   = False
else:
  supersedes = active.prediction_id if active else None
  withdraw   = supersedes is not None

build PredictionMade message (propagation_depth, propagation_chain included)
idempotency_key = f"{asset_id}|{window_start}|ONE_TRADING_DAY|{context_version}"
                  + (f"|prop{propagation_depth}" if propagation_depth > 0 else "")
store_prediction_with_outbox(message, key, withdraw_superseded=withdraw)
  → INSERT INTO predictions (... idempotency_key ...) ON CONFLICT DO NOTHING
  → INSERT INTO contributing_edges rows
  → INSERT INTO outbox_events (PENDING)
  → if withdraw: UPDATE predictions SET status='WITHDRAWN' WHERE prediction_id=$supersedes
  → all in one transaction
return True when a row was inserted
```

Note that a *skipped* direct prediction (unchanged direction+magnitude on an open market) does not
abort the sweep for that context: propagation still runs from the same directional decision, and the
context is still marked PREDICTED at the end.

### 7.4 Graph traversal detail

The service constructs per-event-type condition sets from the loaded context events:

```python
conditions_by_type = {}
for event in events:
    conditions_by_type.setdefault(event.event_type, set()).update(event.context_tags)

# Inject RISK_PREMIUM_ELEVATED globally if the price is elevated
if elevated:
    for et in conditions_by_type:
        conditions_by_type[et].add(RISK_PREMIUM_ELEVATED)

# Query one event type at a time (distinct types only)
all_edges = []
for event_type, conditions in conditions_by_type.items():
    edges = graph.get_firing_edges(event_type, [asset_id], conditions=conditions)
    all_edges.extend(edges)
```

**Neo4j Cypher — direct asset edges:**
```cypher
MATCH (cf:CausalFactor {id: $event_type})-[r:CAUSES]->(a:Asset)
WHERE ($asset_ids IS NULL OR a.id IN $asset_ids)
  AND (r.condition IS NULL OR $conditions IS NULL OR r.condition IN $conditions)
RETURN cf.id, a.id, r.direction, r.weight, r.confidence, r.alpha, r.beta, r.condition
```

**Neo4j Cypher — inherited group edges (when company has no direct edge for same factor+condition):**
```cypher
MATCH (cf:CausalFactor {id: $event_type})-[r:CAUSES]->(g:AssetGroup)<-[:MEMBER_OF]-(a:Asset)
WHERE ($asset_ids IS NULL OR a.id IN $asset_ids)
  AND (r.condition IS NULL OR $conditions IS NULL OR r.condition IN $conditions)
  AND NOT EXISTS {
    MATCH (cf)-[own:CAUSES]->(a)
    WHERE (own.condition IS NULL AND r.condition IS NULL)
       OR own.condition = r.condition
  }
RETURN cf.id, a.id, r.direction, r.weight, r.confidence, r.alpha, r.beta, r.condition, g.id
```

The `NOT EXISTS` subquery ensures company-specific edges shadow group edges. The `edge_id` for an inherited edge uses the group naming convention (`{factor_id}:{group_id}:{condition}` or `{factor_id}:{group_id}`).

### 7.5 Decision algorithm (decide function)

```
Input: asset_id, edges[], deadband, small_max, medium_max, polarity_by_type, elevated

Step 1 — Polarity inversion
  For each edge:
    if polarity_by_type[edge.factor_id] == RESOLUTION:
      effective_direction = flip(edge.direction)   (UP↔DOWN, NEUTRAL unchanged)
    else:
      effective_direction = edge.direction

Step 2 — Scope-B gate (RESOLUTION-driven DOWN filter)
  For each edge with effective_direction == DOWN AND original polarity == RESOLUTION:
    if NOT elevated:
      drop the edge (don't count it at all)

Step 3 — Collect directional edges
  directional = [edges with effective_direction in {UP, DOWN}]
  if directional is empty: return None

Step 4 — Force summation
  for each (edge, direction) in directional:
    reliability = edge.alpha / (edge.alpha + edge.beta)
    strength = edge.weight × reliability
    net  += (+strength if UP else -strength)
    total += strength

  consensus = abs(net) / total   (total > 0 guaranteed by non-empty directional set)
  mass      = total / (total + evidence_halfpoint)

Step 5 — Confidence, then direction bucket
  confidence = min(1.0, consensus × mass)   rounded to 4 d.p.

  if confidence < deadband:  direction = NEUTRAL
  elif net > 0:              direction = UP
  else:                      direction = DOWN

Step 6 — Magnitude
  if direction == NEUTRAL:
    agreeing = all directional edges
  else:
    agreeing = [edges where effective_direction == direction]
  avg_weight = sum(e.weight for e in agreeing) / len(agreeing)
  magnitude = SMALL if avg_weight < 0.40
              MEDIUM if avg_weight < 0.70
              LARGE otherwise

Step 7 — Contributing edges
  Report ALL edges from step 2 (not just directional) with their polarity-adjusted direction,
  so opposing forces are visible in the prediction record.

return Decision(direction, magnitude, confidence, rationale, contributing_edges)
```

**If the result is NEUTRAL**, the decision returns `None` and no prediction is emitted. The context is still marked PREDICTED (no retry needed).

### 7.6 Market-open check

```python
def is_market_open(asset_id, now):
    try:
        timezone = registry.resolve(asset_id).timezone
    except UnknownAssetError:
        logger.warning("market_open_unknown_asset", ...)
        return False   # conservative: unknown → treat as closed

    if not is_trading_day(local_date_in(now, timezone)):
        return False

    return price_reader.is_price_available(asset_id)
    # is_price_available: HTTP GET to Market Data Service
    # returns False if the service is unreachable or has no price for that asset today
```

Holidays and weekends both return `is_trading_day=False`. If the Market Data service is unreachable, `is_price_available` returns False → treated as market-closed (conservative collapse).

### 7.7 Scope-B price elevation check

The `is_elevated` call fetches recent close prices from the Market Data HTTP API:

```
GET {market_data_base_url}/prices/recent/{asset_id}?sessions={price_lookback_sessions}
```

Returns the last N session closes. Elevation check:

```
mean = average of the lookback closes
current = most recent close
is_elevated = (current - mean) / mean >= price_elevated_threshold_pct  (default 0.01 = 1%)
```

If the endpoint is unreachable or returns no data: `elevated = False` (conservative; Scope-B DOWN forces are NOT emitted without confirmed elevation).

### 7.8 Stance management details

**Trading day scenario:**

| Prior stance | New decision | Action |
|---|---|---|
| None | Any non-None | Emit new prediction (no supersede) |
| Same direction | — | Skip; log `prediction_unchanged` |
| Different direction, under daily limit | — | Emit independent new prediction (both scored) |
| Different direction, daily limit reached | — | Skip; log `prediction_daily_limit_reached` |
| Same direction, different magnitude | — | Skip; log `prediction_unchanged` |

**Market closed scenario:**

| Prior stance | Action |
|---|---|
| None | Emit new prediction with `supersedes_prediction_id = NULL` |
| Exists | Emit new prediction with `supersedes_prediction_id = {prior.prediction_id}`; prior marked `WITHDRAWN` |

The market-closed collapse means there is always at most one active (non-withdrawn) prediction per asset for the upcoming session when the market is closed.

### 7.9 Worked example

**Scenario:** War breaks out at 10:47Z on a Wednesday affecting `GOLD` and `BRENT_OIL`.

1. `EventDetected` arrives: `event_type=MILITARY_CONFLICT`, `affected_asset_ids=[GOLD, BRENT_OIL]`, `polarity=OCCURRENCE`, `context_tags=[TRANSPORT_AFFECTED]`, `first_seen_at=10:47Z`
2. Window bounds: `[10:45Z, 11:00Z)` for both assets
3. Contexts created for GOLD (v1) and BRENT_OIL (v1) with window start/end
4. At 11:05Z (11:00 + 5 min grace), scheduled job claims both contexts
5. **For GOLD:**
   - Events: [MILITARY_CONFLICT / OCCURRENCE / TRANSPORT_AFFECTED]
   - conditions_by_type: `{MILITARY_CONFLICT: {TRANSPORT_AFFECTED}}`
   - Price not elevated → no RISK_PREMIUM_ELEVATED added
   - Query Neo4j: edge `MILITARY_CONFLICT -[direction=UP, weight=0.7, alpha=3.0, beta=1.0]-> GOLD`
   - Also: edge `MILITARY_CONFLICT -[condition=TRANSPORT_AFFECTED, direction=UP, weight=0.8]-> GOLD` fires because TRANSPORT_AFFECTED is in conditions
   - reliability = 3.0/(3.0+1.0) = 0.75
   - Forces: 0.7×0.75=0.525 (UP), 0.8×0.75=0.6 (UP); net=1.125, total=1.125, ratio=1.0
   - direction=UP, confidence=1.0 (capped), magnitude: avg_weight=(0.7+0.8)/2=0.75 → LARGE
6. Market is open (Wednesday, price available)
7. No prior active prediction → emit `PredictionMade(GOLD, UP, LARGE, conf=1.0, horizon=ONE_TRADING_DAY)`

### 7.10 Cross-asset propagation algorithm (_run_propagation)

Propagation runs after the direct (pass 0) prediction and before the context is marked PREDICTED. It
is a breadth-first sweep: each pass takes the assets decided directionally in the previous pass and
walks their outgoing `CORRELATES_WITH` edges.

```
Input: record (the source context), direct_decisions {asset: direction},
       visited (already seeded with the direct asset), now, events

current_pass = {asset: (direction, []) for asset, direction in direct_decisions}
depth = 0

while current_pass AND depth < max_propagation_depth:
  depth += 1
  next_pass = {}

  for (source_asset, (source_direction, parent_chain)) in current_pass:

    condition = UPSTREAM_UP if source_direction == UP else UPSTREAM_DOWN

    try:
      corr_edges = graph.get_correlation_edges(source_asset, condition)
    except GraphError:
      log propagation_graph_error (source_asset, depth)
      continue                    ← this source only; the run is NOT failed

    for corr_edge in corr_edges:
      target = corr_edge.target_asset_id
      if target in visited: continue      ← cycle / duplicate guard
      visited.add(target)

      # Synthesise a FiringEdge so decide() is reused verbatim.
      firing = FiringEdge(factor_id=None, asset_id=target,
                          direction=corr_edge.direction, weight=corr_edge.weight,
                          confidence=corr_edge.confidence,
                          alpha=corr_edge.alpha, beta=corr_edge.beta,
                          condition=condition)

      decision = decide(target, [firing], deadband, small_max, medium_max)
      if decision is None or decision.direction == NEUTRAL: continue

      hop   = PropagationHop(source_asset, target, condition, decision.direction,
                             corr_edge.weight)
      chain = parent_chain + [hop]

      # A proxy record re-targets ONLY asset_id: context_id, context_version and the
      # window bounds stay those of the source context.
      stored = store_prediction(proxy(record, target), decision, now, events,
                               propagation_depth=depth, propagation_chain=chain)
      if stored:
        next_pass[target] = (decision.direction, chain)

  current_pass = next_pass
```

**Why `decide()` did not have to change.** A correlation edge is just another signed force, so it is
wrapped as a `FiringEdge` with `factor_id = None`. Two helpers inside `decision.py` gained a
`None` guard for that case, with no change to the decision arithmetic:

- `_effective_direction` skips the RESOLUTION polarity flip when `factor_id is None` — a correlation
  edge has no causal factor and therefore no polarity to invert
- `_rationale` renders the literal `"CORRELATION"` in place of the factor name when
  `factor_id is None` (matching `FiringEdge.edge_id`, which uses the same `CORRELATION` prefix)

Because a propagated decision is computed from exactly one edge, `net == total`, so its confidence is
the edge's own strength ratio (`1.0` before rounding) and its magnitude bucket comes from that single
edge's expert `weight`.

### 7.11 Worked propagation example (verified against the live graph)

**Scenario:** `MILITARY_CONFLICT` with `context_tags=[TRANSPORT_AFFECTED]` affecting `XOM_NYSE`.

1. **Pass 0** — CAUSES edges fire for `XOM_NYSE`; force summation yields UP. `PredictionMade(XOM_NYSE, UP)` is stored with `propagation_depth = 0` and an empty `propagation_chain`. `visited = {XOM_NYSE}`
2. **Pass 1** — direction is UP, so `condition = UPSTREAM_UP`; `get_correlation_edges(XOM_NYSE, UPSTREAM_UP)` returns edges to `NEM_NYSE` and `LUG_STO`
   - `NEM_NYSE` → decision DOWN → `PredictionMade(NEM_NYSE, DOWN, propagation_depth=1)`, chain `[XOM_NYSE →(UPSTREAM_UP) NEM_NYSE]`
   - `LUG_STO` → decision DOWN → `PredictionMade(LUG_STO, DOWN, propagation_depth=1)`, chain `[XOM_NYSE →(UPSTREAM_UP) LUG_STO]`
   - `visited = {XOM_NYSE, NEM_NYSE, LUG_STO}`
3. **Pass 2** — sources are `NEM_NYSE` and `LUG_STO`, both DOWN, so `condition = UPSTREAM_DOWN`. The `NEM_NYSE → XOM_NYSE` back-edge fires but `XOM_NYSE` is already in `visited`, so it is silenced — no second `XOM_NYSE` prediction and no infinite loop
4. Pass 2 produced no new target, so `current_pass` is empty and the loop exits before the depth-3 ceiling. The context is marked PREDICTED

All three predictions share the same `context_id` and `context_version`; their idempotency keys are
`XOM_NYSE|…|1`, `NEM_NYSE|…|1|prop1`, and `LUG_STO|…|1|prop1`.

---

## 8. Interfaces

### 8.1 Consumed message

**Queue:** `prediction.events`  
**Type:** `EventDetected` (see SRS-01, section 5.5)

Key fields used:
- `event_id` — idempotency key for context_events
- `event_type` — graph traversal key
- `affected_asset_ids` — one context per asset
- `first_seen_at` — window alignment
- `polarity` — stored per event; used in force summation
- `context_tags` — stored per event; used to select conditioned edges
- `correlation_id` — not propagated here (PredictionMade gets its own new UUID)

### 8.2 Published message

**Exchange:** `feed.events`  
**Routing key:** `prediction.made`  
**Type:** `PredictionMade` (see SRS-01, section 5.6)

Key fields set by this service:
- `prediction_id` — new UUID
- `context_id` — the context that was closed
- `context_version` — integer version of the context
- `event_ids` — list of event UUIDs that contributed to the context
- `asset_id` — target asset
- `direction` — UP / DOWN / NEUTRAL
- `magnitude` — SMALL / MEDIUM / LARGE
- `confidence` — 0.0 – 1.0 (4 d.p.)
- `horizon` — always `ONE_TRADING_DAY`
- `rationale` — human-readable summary string, max 2000 chars
- `contributing_edges` — list of `ContributingEdge` objects (all firing edges)
- `decision_at` — timestamp of close sweep
- `supersedes_prediction_id` — UUID of withdrawn prior prediction, or null
- `decision_method` — always `GRAPH_ONLY`
- `llm_metadata` — always null (M1)
- `propagation_depth` — `0` for a direct prediction, `1+` for a propagated one (default `0`)
- `propagation_chain` — ordered list of `PropagationHop` objects; empty for a direct prediction (default `[]`)

### 8.3 HTTP endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Returns `{"status": "ok"}` always |
| GET | `/ready` | Returns 200 if DB pool, RabbitMQ consumer, and Neo4j driver are healthy |

### 8.4 Outbound HTTP call (Scope-B gate)

| Method | Path | Service |
|---|---|---|
| GET | `/prices/recent/{asset_id}?sessions={n}` | Market Data Service |

Used to check price elevation before emitting RESOLUTION-driven DOWN predictions.

### 8.5 Scheduled jobs

| Job | Interval | What it does |
|---|---|---|
| `close_and_sweep` | 60 s (configurable) | Claims ready contexts, runs the decision policy, emits predictions, sweeps outbox |

---

## 9. Data Design

### 9.1 Table: `prediction.contexts`

| Column | Type | Notes |
|---|---|---|
| `context_id` | UUID PRIMARY KEY | |
| `asset_id` | TEXT NOT NULL | Registry-validated asset ID |
| `window_start` | TIMESTAMPTZ NOT NULL | UTC-aligned 15-min bucket start |
| `window_end` | TIMESTAMPTZ NOT NULL | `window_start + 15 min` |
| `context_version` | INTEGER NOT NULL | Starts at 1; increments for late events |
| `state` | TEXT DEFAULT 'OPEN' | `OPEN`, `PREDICTED`, `ERROR_RETRYABLE` |
| `watermark` | TIMESTAMPTZ NOT NULL | Originally used for window expiry; updated with each version |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ DEFAULT now() | |
| UNIQUE | `(asset_id, window_start, context_version)` | Idempotency: same window/version → same row |

Index: `contexts_ready_idx ON (state, window_end)` — used by the close-sweep query.

### 9.2 Table: `prediction.context_events`

| Column | Type | Notes |
|---|---|---|
| `context_id` | UUID NOT NULL → contexts | FK with ON DELETE CASCADE |
| `event_id` | UUID NOT NULL | The event's own UUID |
| `event_type` | TEXT NOT NULL | Canonical EventType string |
| `first_seen_at` | TIMESTAMPTZ NOT NULL | Copied from the EventDetected message |
| `polarity` | TEXT DEFAULT 'OCCURRENCE' | `OCCURRENCE` or `RESOLUTION` |
| `context_tags` | TEXT[] DEFAULT '{}' | Condition codes for edge selection |
| `added_at` | TIMESTAMPTZ DEFAULT now() | |
| PRIMARY KEY | `(context_id, event_id)` | Prevents duplicate event-context associations |

Note: `polarity` and `context_tags` were added post-initial via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` backfill migration.

### 9.3 Table: `prediction.predictions`

| Column | Type | Notes |
|---|---|---|
| `prediction_id` | UUID PRIMARY KEY | |
| `context_id` | UUID NOT NULL → contexts | FK |
| `context_version` | INTEGER NOT NULL | Denormalised for query convenience |
| `asset_id` | TEXT NOT NULL | |
| `direction` | TEXT NOT NULL | `UP`, `DOWN`, or `NEUTRAL` |
| `magnitude` | TEXT NOT NULL | `SMALL`, `MEDIUM`, or `LARGE` |
| `confidence` | DOUBLE PRECISION NOT NULL | 0.0 – 1.0 |
| `horizon` | TEXT NOT NULL | Always `ONE_TRADING_DAY` |
| `rationale` | TEXT NOT NULL | Human-readable, max 2000 chars |
| `decision_method` | TEXT NOT NULL | Always `GRAPH_ONLY` |
| `decision_at` | TIMESTAMPTZ NOT NULL | Timestamp of the close sweep |
| `supersedes_prediction_id` | UUID | FK to a prior prediction (market-closed path) |
| `idempotency_key` | TEXT NOT NULL UNIQUE | `{asset_id}|{window_start}|{horizon}|{version}` |
| `status` | TEXT DEFAULT 'PENDING' | `PENDING` or `WITHDRAWN` |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |

### 9.4 Table: `prediction.contributing_edges`

| Column | Type | Notes |
|---|---|---|
| `prediction_id` | UUID NOT NULL → predictions | FK with ON DELETE CASCADE |
| `edge_id` | TEXT NOT NULL | Neo4j edge identifier |
| `direction` | TEXT NOT NULL | Polarity-adjusted effective direction |
| `current_weight` | DOUBLE PRECISION NOT NULL | `reliability = alpha/(alpha+beta)` at decision time |
| `influence_weight` | DOUBLE PRECISION NOT NULL | Expert-assigned `weight` on the edge |
| `path` | TEXT NOT NULL | Human-readable path string (same as `edge_id` for direct edges) |

No PRIMARY KEY constraint in the DDL (rows are always written and read together with their prediction).

### 9.5 Table: `prediction.outbox_events`

| Column | Type | Notes |
|---|---|---|
| `message_id` | UUID PRIMARY KEY | Idempotency key |
| `aggregate_id` | UUID NOT NULL | The `prediction_id` |
| `payload` | TEXT NOT NULL | Full `PredictionMade` message as JSON string |
| `delivery_status` | TEXT DEFAULT 'PENDING' | `PENDING` or `DELIVERED` |
| `attempts` | INTEGER DEFAULT 0 | |
| `last_error` | TEXT | |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |
| `delivered_at` | TIMESTAMPTZ | |

Note: this table stores `payload` as `TEXT` (not `JSONB`) unlike some other services.

---

## 10. Configuration

All variables use the `PREDICTION_` prefix unless noted. Infrastructure variables use no prefix.

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | _(required)_ | PostgreSQL connection string (no prefix) |
| `RABBITMQ_URL` | _(required)_ | RabbitMQ connection string (no prefix) |
| `LOG_LEVEL` | `INFO` | Logging verbosity (no prefix) |
| `PREDICTION_EVENTS_QUEUE` | `prediction.events` | Queue to consume from |
| `PREDICTION_CONTEXT_WINDOW_MINUTES` | `15` | Tumbling window size in minutes |
| `PREDICTION_CLOSE_GRACE_MINUTES` | `5` | Extra minutes after window_end before claiming |
| `PREDICTION_CLOSE_INTERVAL_SECONDS` | `60` | How often the close+sweep job runs |
| `PREDICTION_DECISION_DEADBAND` | `0.15` | Net ratio below which direction = NEUTRAL |
| `PREDICTION_MAGNITUDE_SMALL_MAX` | `0.40` | Average weight threshold for SMALL magnitude |
| `PREDICTION_MAGNITUDE_MEDIUM_MAX` | `0.70` | Average weight threshold for MEDIUM magnitude |
| `PREDICTION_MARKET_DATA_BASE_URL` | `http://feed-market-data:8000` | Market Data service base URL for price reads |
| `PREDICTION_PRICE_LOOKBACK_SESSIONS` | `10` | Number of prior sessions to average for elevation check |
| `PREDICTION_PRICE_ELEVATED_THRESHOLD_PCT` | `0.01` | Fractional threshold: `(current-mean)/mean >= this` = elevated |
| `PREDICTION_MARKET_DATA_TIMEOUT_SECONDS` | `5.0` | HTTP timeout for Market Data calls |
| `PREDICTION_DB_POOL_MIN_SIZE` | `1` | asyncpg minimum pool connections |
| `PREDICTION_DB_POOL_MAX_SIZE` | `5` | asyncpg maximum pool connections |
| `PREDICTION_MAX_PROPAGATION_DEPTH` | `3` | Maximum `CORRELATES_WITH` hops from the direct asset; validated to 1–10 inclusive |

Neo4j connection variables are from the shared `Neo4jSettings` class: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_CONNECTION_TIMEOUT_SECONDS`, `NEO4J_MAX_CONNECTION_POOL_SIZE` (see SRS-01).

---

## 11. Verification

| Requirement | Test file | What is verified |
|---|---|---|
| PRD-4 – PRD-5 (window alignment) | `tests/test_context.py` | Bucket alignment; same event_time → same window; edge of window |
| PRD-17 – PRD-29 (force summation, M1) | `tests/test_decision.py` | Force computation; deadband; magnitude buckets; NEUTRAL → None; RESOLUTION inversion; Scope-B gate |
| PRD-11 – PRD-16 (graph traversal) | `tests/test_pipeline.py` | Distinct-type dedup; condition injection; RISK_PREMIUM_ELEVATED; group edge inheritance |
| PRD-30 – PRD-36 (stance management) | `tests/test_pipeline.py` | Trading-day skip; trading-day new signal; market-closed supersede; market-closed first prediction |
| PRD-37 – PRD-40 (storage + outbox) | `tests/test_pipeline.py` | Atomic transaction; idempotency key conflict; withdraw on supersede |
| PRD-48, PRD-49, PRD-51, PRD-53, PRD-55, PRD-56 (propagation pass) | `tests/test_pipeline.py` | `test_propagation_produces_downstream_prediction` — direct + one propagated prediction; `propagation_depth == 1`; single-hop chain with the expected source, target and `UPSTREAM_UP` condition |
| PRD-50 (visited set / cycle guard) | `tests/test_pipeline.py` | `test_propagation_visited_set_prevents_cycle` — an A→B, B→A pair stores A exactly once |
| PRD-48 (depth cap) | `tests/test_pipeline.py` | `test_propagation_depth_cap_stops_at_max` — with `max_propagation_depth=1` the hop-1 target is stored and the hop-2 target is not |
| PRD-52 (no direct decision → no propagation) | `tests/test_pipeline.py` | `test_no_direct_prediction_skips_propagation` — an empty firing set stores nothing even when correlation edges exist |
| Force summation across converging propagated edges | `tests/test_pipeline.py` | `test_converging_edges_at_same_depth_sum_forces` — two opposing edges reaching one target in the same level net out to the heavier direction, and both are reported as contributors; `test_converging_edges_cancelling_below_deadband_produce_no_prediction` — equal opposing forces emit nothing |
| [§13.2](#132-known-limitations) summation scoped to one depth level | `tests/test_pipeline.py` | `test_first_wins_still_applies_across_different_depths` — a target decided at depth 1 is not revised by a heavier edge arriving at depth 2 |
| Proportional credit across converging correlation edges | `../credibility/tests/test_pipeline.py` | `test_converging_edges_receive_proportional_credit`; `test_converging_edges_share_the_blame_when_wrong`; `test_single_edge_propagation_still_gets_full_credit` |
| PRD-59 (depth configuration) | `tests/test_config.py` | Default of 3; override accepted; `ValidationError` below 1 and above 10 |
| End-to-end | `tests/test_integration.py` | Full EventDetected → PredictionMade flow using fakes |
| End-to-end propagation | `tests/test_integration.py` | `test_propagation_produces_downstream_prediction_for_nem` — live graph: `MILITARY_CONFLICT`/`TRANSPORT_AFFECTED` → `XOM_NYSE` UP at depth 0, then `NEM_NYSE` DOWN at depth 1 |

---

## 12. Failure Handling

| Failure scenario | Behaviour |
|---|---|
| Event with empty `affected_asset_ids` | Discarded; log `event_no_assets`; message acknowledged |
| Duplicate `event_id` on same context | `ON CONFLICT DO NOTHING`; silently idempotent |
| Neo4j unreachable during close sweep (CAUSES query) | `GraphError` caught; context set to `ERROR_RETRYABLE`; close sweep continues with next context |
| Neo4j error during a propagation pass (CORRELATES_WITH query) | `GraphError` caught; logged as `propagation_graph_error` with source asset and depth; that source is skipped. The direct prediction, the other sources, and the rest of the sweep are unaffected; the context is **not** marked `ERROR_RETRYABLE` |
| Correlation cycle (A → B → A) | The target is already in the per-run `visited` set, so the back-edge is silenced; no duplicate prediction, no infinite loop |
| Propagation chain longer than the cap | The loop exits once `depth == max_propagation_depth` (default 3); deeper targets are simply not predicted |
| Propagated decision is NEUTRAL or `None` | No prediction stored for that target and it does not seed the next pass; the target still stays in `visited` |
| Market Data unreachable (is_elevated) | `elevated = False` (conservative — Scope-B DOWN forces not emitted) |
| Market Data unreachable (is_price_available) | `market_open = False` (conservative — collapse-to-one path used) |
| Unknown asset (not in registry) | `market_open = False`; warning log; collapse path |
| Decision returns NEUTRAL | Context marked PREDICTED; no prediction emitted; no retry |
| Decision returns None (Scope-B drops all edges) | Same as NEUTRAL |
| Prediction unchanged (duplicate direction+magnitude) | Context marked PREDICTED; no prediction emitted |
| Idempotency key collision on store | `ON CONFLICT DO NOTHING`; context marked PREDICTED |
| Outbox publish failure | `attempts` incremented, `last_error` written; retried next sweep |
| Database connection lost mid-sweep | Transaction rolls back; context stays OPEN (or state before update); reprocessed next sweep |

---

## 13. Assumptions and Limitations

### 13.1 Accepted design decisions

| Decision | Rationale |
|---|---|
| Graph-only policy (M1), zero LLM at prediction time | POC-6 experimental result: adding LLM arbitration did not improve prediction accuracy vs. the graph-only baseline; STOP was called |
| 15-minute tumbling windows | Short enough to be close to real-time; long enough to catch multi-outlet coverage of the same event; fixed alignment makes replay deterministic |
| Close-grace period (5 min) | Allows articles arriving slightly late (outbox relay delay, slow consumer) to join the same window |
| Polarity inversion: ALL-or-nothing per event type | Conservative: any OCCURRENCE for a factor keeps its original direction; prevents a single contradictory article from silently nullifying a clear causal signal |
| Scope-B gate defaults to no-elevation | If the price feed is unavailable, RESOLUTION-driven DOWN forces are suppressed; this avoids phantom bearish predictions when the data dependency is absent |
| Horizon is always ONE_TRADING_DAY | The POC is calibrated on close-to-close returns; intraday and multi-day horizons are not yet supported |
| Market-closed collapse-to-one | Prevents an ever-growing stack of active predictions over a long weekend; only the most recent stance matters for the next open |
| Propagation reuses `decide()` unchanged | A correlation edge is wrapped as a `FiringEdge` with `factor_id = None` rather than adding a second decision path; the force-summation arithmetic, deadband, and magnitude buckets stay identical for direct and propagated predictions, so both are comparable and scored the same way |
| Visited set is the only cycle guard | A depth cap alone would still allow A→B→A→B within the budget and emit duplicate predictions for the same asset in one run; a per-run visited set makes each asset decidable at most once, so the cheapest correct answer wins |
| Propagated predictions reuse the source `context_id` | The propagated prediction is caused by the same events as the direct one, so it belongs to the same context; only `asset_id` is re-targeted (via a proxy record), which keeps `event_ids` and the window bounds truthful and avoids inventing synthetic contexts |
| Depth default 3, hard ceiling 10 | Three hops covers the seeded correlation chains while bounding the Neo4j query fan-out per close sweep; the validated 1–10 range prevents a misconfiguration from turning one context into an unbounded traversal |
| Propagation adds zero LLM calls | M1 / GRAPH_ONLY is unchanged: propagation is pure graph traversal plus the existing force summation (POC-6 STOP still holds) |

### 13.2 Known limitations

- **Industry fan-out produces correlated predictions** — if Cleansing assigns 10 asset IDs to one event, Prediction emits 10 predictions. The Verification Service scores each independently, but all 10 share the same causal evidence, so their outcomes are highly correlated.
- **No multi-hop CAUSES traversal** — the CAUSES Cypher query is still a single hop: `CausalFactor → Asset`. Indirect *causal* chains (e.g. military conflict → oil supply → airline cost → airline stock) are not modelled. Multi-hop reach now exists only along `CORRELATES_WITH` edges (section 7.10), which are asset-to-asset and seeded by the offline structure learner.
- **Force summation on propagated hops is scoped to one depth level** — converging correlation edges combine only when they reach the target in the *same* level. A target decided at depth *n* is added to the visited set, so a (possibly heavier) edge arriving at depth *n+1* cannot revise it — that ordering dependence is the price of the cycle guard. Within a level the outcome is order-independent.
- **Propagation fan-out is unbounded within a pass** — the depth cap limits hops, not breadth. One heavily correlated source asset can emit a prediction per outgoing correlation edge, and each of those issues its own Neo4j query in the next pass.
- **Propagation is not persisted per-hop in Postgres** — `propagation_depth` and `propagation_chain` travel on the `PredictionMade` message (and are forwarded on `PredictionScored` for Credibility) but are not columns on `prediction.predictions`; the chain cannot be queried from the prediction schema.
- **`RISK_PREMIUM_ELEVATED` injection is per-close-sweep** — the elevation is checked once at context close time; an intraday price spike after the window is ignored.
- **Market Data timeout blocks the sweep** — the price-elevation call has a 5-second timeout; a slow Market Data service delays the entire close-sweep job by up to 5 seconds per context.
- **Context version 1 only in practice** — context versioning (late events opening a new version) is structurally supported but late events are uncommon in the POC; version 2+ contexts have not been exercised in production.
- **No calendar for public holidays** — `is_trading_day` checks only weekday vs weekend; country-specific public holidays are not modelled (see SRS-01 and SyRS limitations).

---

## 14. How to Update This Document

### 14.1 When to update

Update this document whenever any of the following changes:

- A new field is added to `EventDetected` or `PredictionMade` that this service reads or writes
- The decision policy changes (new deadband, magnitude thresholds, or a second decision method beyond M1)
- The context windowing changes (different window size, alignment, or grace period)
- The stance management logic changes (e.g. new rules for supersede vs. independent)
- The Scope-B price gate logic changes (new threshold, new HTTP endpoint, different fallback)
- A new Neo4j Cypher query is added or the existing ones change
- The cross-asset propagation logic changes (new condition codes, a different cycle guard, a different depth cap, or the propagation stops reusing `decide()`)
- An environment variable is added, removed, or has its default changed in `config.py`
- A new table column is added or modified in `db.py`
- A new test file is added (add it to section 11)
- An accepted design decision is revisited (especially POC-6 STOP)

### 14.2 Steps to update

1. **Read the current source first** — verify what the code actually does before writing requirements
2. **Assign the next PRD-N ID** — check the highest existing ID in this file and continue the sequence
3. **Update the relevant section** — requirements table, DDL table, configuration table, or how-it-works prose
4. **Add a row to section 15** (Change History) with date, what changed, and why
5. **Do not renumber existing IDs** — if a requirement is removed, mark it `Status: Withdrawn`
6. **Update `requirements/README.md`** if the ID range for PRD changes

---

## 15. Change History

| Date | Description |
|---|---|
| 2026-08-05 | Initial as-built specification for E04 (Prediction Service); PRD-1 through PRD-47 |
| 2026-08-12 | **Feature: cross-asset propagation (E10).** `close_ready_contexts` now runs pass 0 (the direct CAUSES prediction) followed by a depth-capped breadth-first sweep over `CORRELATES_WITH` edges, so a directional prediction on one asset produces secondary predictions on its correlated assets. `decide()` is reused verbatim — a correlation edge is wrapped as a `FiringEdge` with `factor_id = None`, and only two helpers in `decision.py` gained None-guards (`_effective_direction` skips the polarity flip, `_rationale` renders `CORRELATION`). Cycles are prevented by a per-pipeline-run `visited` set seeded with the direct asset; a `GraphError` in a propagation pass skips one source instead of failing the run. M1 / GRAPH_ONLY is unchanged: zero LLM calls added. New section 5.8 with PRD-48…PRD-59; section 5.8 (health and readiness) renumbered to 5.9; §2.1, §3, §4, §7.3, §8.2, §10, §11, §12, §13 and §14.1 updated; §7.10 (propagation algorithm) and §7.11 (worked propagation example) added |
