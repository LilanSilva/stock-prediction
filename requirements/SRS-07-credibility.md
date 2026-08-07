# SRS-07 — Credibility Service

**Document ID:** SRS-07  
**Status:** Implemented  
**Priority:** Must  
**Component prefix:** CRD  
**Related system requirements:** SYS-64 – SYS-72  
**Epic:** E07 (Credibility Service)

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
| Created | 2026-08-05 |
| Last updated | 2026-08-07 |
| Replaces | `docs/functional-documents/credibility-service-functional-document.md` (deleted 2026-08-06) |
| Source code | `src/services/credibility/` |
| Config class | `credibility.config.CredibilitySettings`, `credibility.learning.config.LearningSettings` |
| DB schema | `credibility` (owned by this service; the offline learner reads `cleansing` and `market_data` in read-only mode) |

---

## 2. Purpose and Scope

### 2.1 What this service does

The Credibility Service is the final stage in the feedback loop. It receives `PredictionScored` messages from the Verification Service and updates the causal knowledge graph so future predictions benefit from observed accuracy.

Specific responsibilities:

**Online consumer (always-on):**
- Consume `PredictionScored` messages
- Apply proportional Beta-Bernoulli credit to each contributing CAUSES edge in Neo4j
- Apply equal Beta-Bernoulli credit to each contributing news source in PostgreSQL
- Write an immutable history row per updated entity for auditing and 95% confidence-interval tracking
- Guard idempotency: each `prediction_id` is processed at most once

**Offline structure learner (scheduled or CLI):**
- Mine historical events and price outcomes from the database
- Estimate (factor, condition, asset) edge parameters from aggregated sample statistics
- Write or update conditioned CAUSES edges in Neo4j with data-derived direction, weight, and Beta-Bernoulli counts

### 2.2 What it does not do

- Does not produce predictions (Prediction Service)
- Does not score predictions (Verification Service)
- Does not fetch market prices (Market Data Service)
- Does not emit any downstream messages (the service writes to Neo4j and PostgreSQL only)

---

## 3. Definitions

| Term | Meaning |
|---|---|
| Beta-Bernoulli | A conjugate Bayesian model: `alpha` = success count + prior, `beta` = failure count + prior; reliability = `alpha / (alpha + beta)` |
| prior_floor | The minimum allowed value for both `alpha` and `beta` (default 1.0); prevents a seeded edge from being driven below the uninformed prior |
| Credit | The fraction of the total "reward" allocated to one entity; credits sum to 1.0 across a prediction's contributing entities |
| Proportional credit (edges) | `edge_credit = edge.influence_weight / sum(all influence_weights)`; an edge with a larger expert weight earns a larger fraction |
| Equal credit (sources) | `source_credit = 1 / len(sources)`; all news sources contributing to a prediction share credit equally |
| Edge entity_id | Business key format: `"FACTOR->ASSET"` (unconditional) or `"FACTOR|CONDITION->ASSET"` (conditioned) |
| Source entity_id | Lowercase news domain string (e.g. `"reuters.com"`) |
| Offline structure learner | A batch job (`python -m credibility.learning.run`) that estimates edge parameters from historical event/price data and writes conditioned edges to Neo4j |
| Sample | One (factor, condition, asset) realised observation: event type + condition tag → price return |
| Abnormal sample | A sample where `|actual_return| >= abnormal_threshold × historical_volatility`; bypasses the `min_samples` threshold |
| EdgeEstimate | Estimated direction, weight, confidence, alpha, beta for one (factor, condition, asset) group |
| WEIGHT_RETURN_SCALE | 0.05 (5%); a 5% mean signed daily move maps to expert weight = 1.0 |

---

## 4. System Context

```
[Verification Service]
       |
       | PredictionScored (routing key: prediction.scored)
       | Queue: credibility.scored
       v
[Credibility Service (online consumer)]
  - idempotency check (processed_predictions)
  - proportional credit → each contributing edge in Neo4j
  - equal credit → each source in PostgreSQL
  - commit Postgres (idempotency guard + upserts + history)
       |
       ├── writes → Neo4j (CAUSES edge alpha/beta)
       └── writes → PostgreSQL (credibility.credibility upsert + credibility_history append)

[Offline structure learner]  ← runs on schedule or via CLI
  - reads: cleansing.events + market_data.close_observations
  - estimates edge parameters from historical data
  - writes conditioned edges → Neo4j (MERGE)
```

- Consumes from: `credibility.scored`
- No outbound messages (no outbox)
- Writes: Neo4j (CAUSES edge alpha/beta updates), PostgreSQL (credibility + credibility_history tables)
- The offline learner: reads `cleansing.events` and `market_data.close_observations` (read-only cross-schema), writes Neo4j conditioned edges

---

## 5. Functional Requirements

### 5.1 Online consumer: idempotency

| ID | Requirement | Status |
|---|---|---|
| CRD-1 | The service shall consume `PredictionScored` messages from the `credibility.scored` queue | Implemented |
| CRD-2 | Before processing, the service shall check whether the `prediction_id` already exists in `processed_predictions`; if found, skip and return False (no-op) | Implemented |
| CRD-3 | The `processed_predictions` insert is the final step; if a crash occurs before it, the replay reprocesses normally | Implemented |

### 5.2 Online consumer: edge credit (Neo4j)

| ID | Requirement | Status |
|---|---|---|
| CRD-4 | For each contributing edge in the `PredictionScored` message, the service shall compute `credit = edge.influence_weight / sum(all influence_weights)` | Implemented |
| CRD-5 | If the total influence weight is zero, credits shall be split equally (`1 / n`) | Implemented |
| CRD-6 | For each edge, the service shall read the current `(alpha, beta)` from Neo4j by matching on the full `edge_id` business key | Implemented |
| CRD-7 | If `is_correct = True`: `alpha += credit`; if `is_correct = False`: `beta += credit` | Implemented |
| CRD-8 | Both `alpha` and `beta` shall be floored at `prior_floor` (default 1.0) after the update so seeded values can only grow | Implemented |
| CRD-9 | The new `(alpha, beta)` shall be written back to Neo4j via the shared graph client's `update_edge_weight` | Implemented |
| CRD-10 | If a contributing edge is not found in Neo4j (the graph was modified after the prediction), a WARNING shall be logged and that edge skipped; remaining edges still proceed | Implemented |
| CRD-11 | The `edge_id` parser shall accept `"FACTOR->TARGET"` (unconditional) and `"FACTOR|CONDITION->TARGET"` (conditioned), where `TARGET` is either a registry asset ID or an industry **group** ID; any other format shall raise `InvalidScoredMessageError` (terminal) | Implemented |
| CRD-45 | A `TARGET` that is neither a declared asset nor a declared asset group shall raise `InvalidScoredMessageError` (terminal) | Implemented |
| CRD-46 | When `TARGET` is a group ID, the service shall read and update the `(:CausalFactor)-[:CAUSES]->(:AssetGroup)` edge itself, **not** a per-asset edge — an inherited edge's credit belongs to the industry prior that fired | Implemented |
| CRD-47 | The current `(alpha, beta)` for a group edge shall be read by matching `(factor, group, condition)` directly, never through a member asset | Implemented |

### 5.3 Online consumer: source credit (PostgreSQL)

| ID | Requirement | Status |
|---|---|---|
| CRD-12 | For each source in `PredictionScored.source_ids`, the service shall compute `credit = 1 / len(sources)` | Implemented |
| CRD-13 | Source domains shall be normalised to lowercase before credit assignment | Implemented |
| CRD-14 | If the source has no existing state in `credibility.credibility`, the prior `(prior_floor, prior_floor)` shall be used as the starting state | Implemented |
| CRD-15 | The same hit/miss rule applies: `alpha += credit` on correct, `beta += credit` on incorrect, both floored at `prior_floor` | Implemented |
| CRD-16 | If `source_ids` is empty, a WARNING `scored_message_has_no_sources` shall be logged and source updates skipped | Implemented |

### 5.4 Online consumer: Postgres commit

| ID | Requirement | Status |
|---|---|---|
| CRD-17 | After Neo4j writes succeed, the service shall commit the following in one PostgreSQL transaction: (1) insert `processed_predictions` row, (2) upsert `credibility` rows for all entities (edges + sources), (3) append `credibility_history` rows for all entities | Implemented |
| CRD-18 | If the Postgres commit fails after Neo4j writes have already landed, the service shall log CRITICAL with the `prediction_id` and re-raise; this is a documented partial-update risk requiring manual replay | Implemented |
| CRD-19 | If the idempotency row insert loses a race to a concurrent delivery, the function returns False without any further action | Implemented |

### 5.5 Credibility history

| ID | Requirement | Status |
|---|---|---|
| CRD-20 | For each updated entity, the service shall append one row to `credibility_history` with `alpha_before`, `beta_before`, `alpha_after`, `beta_after`, `credibility_before`, `credibility_after`, `ci_lower`, and `ci_upper` | Implemented |
| CRD-21 | `ci_lower` and `ci_upper` shall be the 95% Wilson score confidence interval for the Bernoulli proportion `alpha / (alpha + beta)` | Implemented |
| CRD-22 | The `credibility_history` table is append-only and never modified; it provides a complete audit trail of every credibility change | Implemented |

### 5.6 Offline structure learner

| ID | Requirement | Status |
|---|---|---|
| CRD-23 | The offline learner shall be invocable as `python -m credibility.learning.run` | Implemented |
| CRD-24 | The learner shall also run on a schedule inside the service if `CREDIBILITY_LEARNING_ENABLED=true` (default) every `CREDIBILITY_LEARNING_INTERVAL_HOURS` hours | Implemented |
| CRD-25 | The learner shall read all historical events from `cleansing.events` within the last `lookback_days` days | Implemented |
| CRD-26 | For each event and each affected asset, the learner shall resolve the baseline and settlement sessions using the asset's own timezone (same `resolve_baseline_settlement` function as Verification) | Implemented |
| CRD-27 | For each event, the learner shall emit one `Sample` with `condition=None` (unconditional) plus one additional `Sample` per unique `context_tag` | Implemented |
| CRD-28 | Samples with missing price data on either session shall be skipped | Implemented |
| CRD-29 | The learner shall compute per-asset historical volatility from `CREDIBILITY_LEARNING_VOLATILITY_LOOKBACK_DAYS` of close observations | Implemented |
| CRD-30 | A sample is flagged `is_abnormal=True` when `abs(actual_return) >= abnormal_threshold × volatility` (default threshold = 2.0×) | Implemented |
| CRD-31 | Groups with fewer than `min_samples` (default 5) observations shall be dropped, unless any sample in the group is flagged abnormal (in which case the minimum is 1) | Implemented |
| CRD-32 | Direction: UP when `mean_signed_return > deadband`; DOWN when `< -deadband`; NEUTRAL otherwise (NEUTRAL edges are dropped, not written) | Implemented |
| CRD-33 | `weight = min(1.0, abs(mean_signed_return) / 0.05)` — a 5% mean move maps to weight 1.0 | Implemented |
| CRD-34 | `confidence = agreeing_count / total_samples` where agreeing = samples with return matching the estimated direction | Implemented |
| CRD-35 | `alpha = agreeing + 1.0` (prior); `beta = disagreeing + 1.0` (prior) | Implemented |
| CRD-36 | RESOLUTION-polarity samples are inverted before aggregation: `signed_return = -actual_return` for RESOLUTION, `+actual_return` for OCCURRENCE | Implemented |
| CRD-37 | Estimates shall be written to Neo4j using `upsert_conditioned_edge` (MERGE semantics): creates the edge if absent, updates existing weights | Implemented |
| CRD-38 | NEUTRAL estimated edges are not written to Neo4j (no neutral CAUSES edges in the graph) | Implemented |

### 5.7 Health and readiness

| ID | Requirement | Status |
|---|---|---|
| CRD-39 | The service shall expose `GET /health` returning `{"status": "ok"}` | Implemented |
| CRD-40 | The service shall expose `GET /ready` returning 200 only when the database pool, RabbitMQ consumer, and Neo4j driver are all healthy | Implemented |

---

## 6. Non-Functional Requirements

| ID | Requirement | Status |
|---|---|---|
| CRD-41 | Secrets (`DATABASE_URL`, `RABBITMQ_URL`, Neo4j password) shall be environment variables; none committed | Implemented |
| CRD-42 | The service shall bind to the local environment only | Implemented |
| CRD-43 | The update sequence (Neo4j first, then Postgres) is documented; the dual-write gap is an accepted POC limitation, not a silent risk | Implemented |
| CRD-44 | The offline learner shall produce a deterministic output for the same input data (same samples → same EdgeEstimate values, sorted by `(factor, condition, asset)`) | Implemented |

---

## 7. How It Works

### 7.1 Online consumer pipeline

```
[PredictionScored arrives]
  ↓
CRD-2: Check processed_predictions — if found, skip (return False)
  ↓
CRD-4–CRD-10: _update_edges(message)
  For each edge in message.contributing_edges:
    credit = edge.influence_weight / total_influence
    factor, condition, asset = parse_edge_id(edge.edge_id)
    current_edge = graph.get_firing_edges(factor, [asset])
    find matching edge by edge_id
    if not found: log warning, skip
    alpha_after, beta_after = apply_bernoulli(alpha, beta, credit, is_correct, floor)
    graph.update_edge_weight(factor, asset, alpha=alpha_after, beta=beta_after, condition)
    record WeightUpdate
  ↓
CRD-12–CRD-15: _update_sources(message)
  For each source_id in message.source_ids:
    credit = 1 / len(sources)
    current = repo.get_source_state(source_id)  or (1.0, 1.0)
    alpha_after, beta_after = apply_bernoulli(alpha, beta, credit, is_correct, floor)
    record WeightUpdate
  ↓
CRD-17: commit_updates(prediction_id, all_updates) — single Postgres transaction:
    INSERT INTO processed_predictions (prediction_id) — ON CONFLICT → race lost → return False
    for each update:
        UPSERT credibility (entity_id, entity_type, alpha_after, beta_after, score_after)
        INSERT credibility_history (before/after + CI)
  ↓
log prediction_credibility_applied
```

### 7.2 Beta-Bernoulli update

```python
def apply_bernoulli(alpha, beta, credit, *, is_correct, floor):
    if is_correct:
        alpha += credit   # observed success
    else:
        beta += credit    # observed failure
    return max(alpha, floor), max(beta, floor)
```

**Example (MILITARY_CONFLICT → GOLD edge, starting at alpha=3.0, beta=1.0):**
- Prior reliability = 3.0 / (3.0 + 1.0) = 0.75
- Prediction with 2 edges, GOLD contribution = influence_weight=0.7, total=1.2 → credit = 0.583
- Prediction was correct (is_correct=True)
- `alpha_after = 3.0 + 0.583 = 3.583`, `beta_after = 1.0` (unchanged)
- New reliability = 3.583 / (3.583 + 1.0) = 0.782

**Example (incorrect prediction, same starting state):**
- `alpha_after = 3.0`, `beta_after = 1.0 + 0.583 = 1.583`
- New reliability = 3.0 / (3.0 + 1.583) = 0.655

### 7.3 Wilson score confidence interval (95%)

Stored in `credibility_history.ci_lower` and `ci_upper`:

```
n = alpha + beta                       # effective observation count
p = alpha / n                          # posterior mean (= credibility score)
z = 1.96                               # 95% two-tailed
denominator = 1 + z² / n
centre = (p + z² / (2n)) / denominator
half_width = z × sqrt(p(1-p)/n + z²/(4n²)) / denominator
ci_lower = max(0, centre - half_width)
ci_upper = min(1, centre + half_width)
```

### 7.4 Edge ID parser (parse_edge_id)

```
format 1 (unconditional, asset):  "MILITARY_CONFLICT->GOLD"
  → factor = MILITARY_CONFLICT, condition = None, target = AssetId("GOLD")

format 2 (conditioned, asset):    "MILITARY_CONFLICT|TRANSPORT_AFFECTED->BRENT_OIL"
  → factor = MILITARY_CONFLICT, condition = TRANSPORT_AFFECTED, target = AssetId("BRENT_OIL")

format 3 (inherited group edge):  "MILITARY_CONFLICT->WEAPON_INDUSTRY"
  → factor = MILITARY_CONFLICT, condition = None, target = "WEAPON_INDUSTRY"  (plain str)
```

The parser splits on `->` (right), then on `|` (left part). An unknown factor or condition raises
`InvalidScoredMessageError` (terminal).

**The target may be an asset or an industry group.** Prediction reports the *group* edge in
`edge_id` whenever an asset inherited it (see [REF-02 §4.1](REF-02-asset-registry.md)), so the target
is resolved as an `AssetId` when the registry knows it as an asset, otherwise as a group ID when the
registry knows it as a group. A target that is neither is terminal (`CRD-45`).

Callers distinguish the two by type: an `AssetId` reads via `get_firing_edges`, a group ID reads via
`get_group_edge_counts` and writes with `target_is_group=True`. A group edge must be addressed
directly rather than through a member asset, because `get_firing_edges` deliberately hides a group
edge from any member that owns an edge for the same `(factor, condition)` pair — so a member-based
lookup can miss the very edge that fired (`CRD-47`).

### 7.5 Offline structure learner pipeline

**Invocation:** `python -m credibility.learning.run`  
**Or:** triggered by APScheduler inside the service every `learning_interval_hours` hours.

```
Step 1 — build_samples(pool, lookback_days=30)
  SELECT events from cleansing.events WHERE first_seen_at >= (now - 30 days)
  For each event, for each affected asset:
    baseline, settlement = resolve_baseline_settlement(event.first_seen_at, asset.timezone)
    baseline_close  = SELECT close FROM market_data.close_observations WHERE asset=X, session=baseline
    settlement_close = SELECT close FROM market_data.close_observations WHERE asset=X, session=settlement
    if either missing: skip
    actual_return = (settlement_close - baseline_close) / baseline_close
    volatility = std_dev(daily_returns of last 30 days closes)
    is_abnormal = (volatility > 0 AND |actual_return| >= 2.0 × volatility)
    emit Sample(factor=event_type, condition=None, ...)
    for each context_tag in event.context_tags:
        emit Sample(factor=event_type, condition=tag, ...)

Step 2 — estimate_edges(samples, deadband=0.002, min_samples=5)
  Group by (factor, condition, asset)
  For each group:
    if count < min_samples AND no abnormal samples: skip
    signed = [invert_if_resolution(sample.actual_return) for sample in group]
    mean = average(signed)
    positives = count(s > 0)
    negatives = count(s < 0)
    if mean > 0.002: direction=UP, agreeing=positives
    elif mean < -0.002: direction=DOWN, agreeing=negatives
    else: direction=NEUTRAL → skip (not written)
    weight = min(1.0, |mean| / 0.05)
    confidence = agreeing / total
    alpha = agreeing + 1.0
    beta = (total - agreeing) + 1.0

Step 3 — write_estimates(graph, estimates)
  For each EdgeEstimate with direction != NEUTRAL:
    graph.upsert_conditioned_edge(factor, condition, asset,
        direction=direction, weight=weight, confidence=confidence,
        alpha=alpha, beta=beta)
  Returns count of edges written

Step 4 — log learning_run_complete with sample/estimate/written counts
```

### 7.6 RESOLUTION inversion in the learner

When an event had `polarity = RESOLUTION`, the actual_return is inverted before computing the mean:

```python
signed_return = -actual_return if polarity == RESOLUTION else actual_return
```

This keeps all edge learning in the OCCURRENCE orientation. A war being called off that caused gold prices to DROP (negative actual_return) is inverted to +: the `MILITARY_CONFLICT → GOLD` edge still learns "UP" on occurrence.

### 7.7 Worked example (online consumer)

**Input:** `PredictionScored` for GOLD, `is_correct=True`, contributing_edges = [edge_id=`MILITARY_CONFLICT->GOLD`, influence_weight=0.7].

1. `processed_predictions` check → not found → proceed
2. Edge credit: total=0.7, credit=0.7/0.7=1.0
3. `parse_edge_id("MILITARY_CONFLICT->GOLD")` → factor=MILITARY_CONFLICT, condition=None, asset=GOLD
4. `graph.get_firing_edges(MILITARY_CONFLICT, [GOLD])` → edge with alpha=3.0, beta=1.0
5. `apply_bernoulli(3.0, 1.0, 1.0, is_correct=True, floor=1.0)` → alpha=4.0, beta=1.0
6. `graph.update_edge_weight(MILITARY_CONFLICT, GOLD, alpha=4.0, beta=1.0, condition=None)`
7. Source updates: `source_ids=[]` → log warning, skip
8. Postgres commit: insert `processed_predictions`, upsert `credibility(MILITARY_CONFLICT->GOLD, edge, 4.0, 1.0, 0.80)`, append history row
9. Log `prediction_credibility_applied`, edges_updated=1, sources_updated=0

### 7.8 Worked example (inherited group edge)

**Input:** `PredictionScored` for `LMT_NYSE`, `is_correct=True`, contributing_edges =
[edge_id=`MILITARY_CONFLICT->WEAPON_INDUSTRY`, influence_weight=0.6]. Prediction fired the industry
edge because `LMT_NYSE` had no edge of its own for that `(factor, condition)` pair.

1. `processed_predictions` check → not found → proceed
2. Edge credit: total=0.6, credit=0.6/0.6=1.0
3. `parse_edge_id(...)` → factor=MILITARY_CONFLICT, condition=None, target=`"WEAPON_INDUSTRY"` (a
   group ID, not an `AssetId`)
4. `graph.get_group_edge_counts(MILITARY_CONFLICT, "WEAPON_INDUSTRY", None)` → alpha=1.0, beta=1.0
5. `apply_bernoulli(1.0, 1.0, 1.0, is_correct=True, floor=1.0)` → alpha=2.0, beta=1.0
6. `graph.update_edge_weight(..., "WEAPON_INDUSTRY", alpha=2.0, beta=1.0, target_is_group=True)` —
   the `:AssetGroup` edge is updated; **`LMT_NYSE`'s own edges are untouched**
7. Postgres commit: upsert `credibility(MILITARY_CONFLICT->WEAPON_INDUSTRY, edge, 2.0, 1.0, 0.67)`

Every listing in the group therefore contributes evidence to one shared prior, which is what lets a
newly listed company predict before it has company-specific history.

---

## 8. Interfaces

### 8.1 Consumed message

**Queue:** `credibility.scored`  
**Type:** `PredictionScored` (see SRS-01, section 5.9)

Key fields used:
- `prediction_id` — idempotency key
- `is_correct` — determines whether credit goes to alpha or beta
- `contributing_edges` — list of `ContributingEdge`; `edge_id` and `influence_weight` used
- `source_ids` — list of news source domain strings (equal credit)

### 8.2 Published messages

None. The Credibility Service only writes to Neo4j and PostgreSQL; it does not publish any messages.

### 8.3 HTTP endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Returns `{"status": "ok"}` always |
| GET | `/ready` | Returns 200 if DB pool, RabbitMQ consumer, and Neo4j driver are healthy |

### 8.4 Scheduled jobs

| Job | Interval | What it does |
|---|---|---|
| `offline_learner` | `learning_interval_hours` (default 24 h) | Runs one build → estimate → write pass; only if `learning_enabled=true` |

### 8.5 CLI

| Command | What it does |
|---|---|
| `python -m credibility.learning.run` | Runs one offline learning pass using the current database state |

---

## 9. Data Design

### 9.1 Table: `credibility.credibility`

| Column | Type | Notes |
|---|---|---|
| `entity_id` | TEXT NOT NULL | `"FACTOR->ASSET"`, `"FACTOR|COND->ASSET"`, or a domain string |
| `entity_type` | TEXT NOT NULL | `"edge"` or `"source"` |
| `alpha` | DOUBLE PRECISION DEFAULT 1.0 | Success count + prior |
| `beta` | DOUBLE PRECISION DEFAULT 1.0 | Failure count + prior |
| `credibility_score` | DOUBLE PRECISION NOT NULL | `alpha / (alpha + beta)` |
| `last_updated` | TIMESTAMPTZ DEFAULT now() | |
| PRIMARY KEY | `(entity_id, entity_type)` | One row per entity |

This table holds the CURRENT state only. The full history is in `credibility_history`.

### 9.2 Table: `credibility.credibility_history`

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL PRIMARY KEY | Insertion order |
| `entity_id` | TEXT NOT NULL | Same as `credibility` table |
| `entity_type` | TEXT NOT NULL | `"edge"` or `"source"` |
| `prediction_id` | UUID NOT NULL | Which scored prediction triggered this update |
| `alpha_before` | DOUBLE PRECISION NOT NULL | State before this update |
| `beta_before` | DOUBLE PRECISION NOT NULL | |
| `alpha_after` | DOUBLE PRECISION NOT NULL | State after this update |
| `beta_after` | DOUBLE PRECISION NOT NULL | |
| `credibility_before` | DOUBLE PRECISION NOT NULL | `alpha_before / (alpha_before + beta_before)` |
| `credibility_after` | DOUBLE PRECISION NOT NULL | `alpha_after / (alpha_after + beta_after)` |
| `ci_lower` | DOUBLE PRECISION NOT NULL | 95% Wilson CI lower bound |
| `ci_upper` | DOUBLE PRECISION NOT NULL | 95% Wilson CI upper bound |
| `updated_at` | TIMESTAMPTZ DEFAULT now() | |

Indexes:
- `credibility_history_entity_idx ON (entity_id, entity_type, updated_at DESC)` — time-ordered per-entity history
- `credibility_history_prediction_idx ON (prediction_id)` — look up all updates for one prediction

### 9.3 Table: `credibility.processed_predictions`

| Column | Type | Notes |
|---|---|---|
| `prediction_id` | UUID PRIMARY KEY | One row per fully processed prediction |
| `processed_at` | TIMESTAMPTZ DEFAULT now() | |

This is the idempotency guard. Once the row is inserted (as the final step of the Postgres commit), the prediction is considered done. A crash before this insert means the message is reprocessed on replay; a crash after means the replay is a no-op.

---

## 10. Configuration

### 10.1 Online consumer (CredibilitySettings, prefix `CREDIBILITY_`)

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | _(required)_ | PostgreSQL connection string (no prefix) |
| `RABBITMQ_URL` | _(required)_ | RabbitMQ connection string (no prefix) |
| `LOG_LEVEL` | `INFO` | Logging verbosity (no prefix) |
| `CREDIBILITY_SCORED_QUEUE` | `credibility.scored` | Queue to consume from |
| `CREDIBILITY_PRIOR_FLOOR` | `1.0` | Minimum value for alpha and beta; floored on every update |
| `CREDIBILITY_DB_POOL_MIN_SIZE` | `1` | asyncpg minimum pool connections |
| `CREDIBILITY_DB_POOL_MAX_SIZE` | `5` | asyncpg maximum pool connections |
| `CREDIBILITY_LEARNING_ENABLED` | `true` | Whether the offline learner runs on schedule |
| `CREDIBILITY_LEARNING_INTERVAL_HOURS` | `24` | How often the learner runs (hours) |

Neo4j connection variables: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_CONNECTION_TIMEOUT_SECONDS`, `NEO4J_MAX_CONNECTION_POOL_SIZE` (from `shared.graph.Neo4jSettings`; see SRS-01).

### 10.2 Offline learner (LearningSettings, prefix `CREDIBILITY_LEARNING_`)

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | _(required)_ | PostgreSQL connection string (no prefix) |
| `LOG_LEVEL` | `INFO` | Logging verbosity (no prefix) |
| `CREDIBILITY_LEARNING_DEADBAND` | `0.002` | Mean signed return must exceed ±0.2% for a directional edge |
| `CREDIBILITY_LEARNING_MIN_SAMPLES` | `5` | Minimum observations per group (bypassed for abnormal samples) |
| `CREDIBILITY_LEARNING_LOOKBACK_DAYS` | `30` | How far back in `cleansing.events` to look |
| `CREDIBILITY_LEARNING_VOLATILITY_LOOKBACK_DAYS` | `30` | Days of closes used to compute per-asset historical volatility |
| `CREDIBILITY_LEARNING_ABNORMAL_THRESHOLD` | `2.0` | Multiplier: `|return| >= threshold × volatility` = abnormal |
| `CREDIBILITY_LEARNING_DB_POOL_MIN_SIZE` | `1` | asyncpg minimum pool connections |
| `CREDIBILITY_LEARNING_DB_POOL_MAX_SIZE` | `5` | asyncpg maximum pool connections |

---

## 11. Verification

| Requirement | Test file | What is verified |
|---|---|---|
| CRD-2 – CRD-3 (idempotency) | `tests/test_pipeline.py` | Duplicate prediction_id → no-op; guard inserted last |
| CRD-4 – CRD-8 (edge credit math) | `tests/test_updater.py` | Proportional credit; zero-total fallback; floor enforcement; hit/miss |
| CRD-12 – CRD-15 (source credit math) | `tests/test_updater.py` | Equal credit; lowercase normalisation; empty sources warning |
| CRD-11 (edge_id parser) | `tests/test_pipeline.py` | Unconditional and conditioned formats; malformed → InvalidScoredMessageError |
| CRD-45 (unknown target) | `tests/test_pipeline.py` | `test_parse_edge_id_rejects_target_that_is_neither_asset_nor_group` |
| CRD-46 (group credit) | `tests/test_pipeline.py` | `test_inherited_group_edge_credit_lands_on_the_group_prior` — group alpha rises, the asset's own edge is unchanged |
| CRD-47 (direct group read) | `src/shared/tests/test_graph_client.py` | `test_get_group_edge_counts_returns_counts`, `test_update_edge_weight_targets_asset_group_when_flagged` |
| CRD-17 – CRD-19 (Postgres commit) | `tests/test_pipeline.py` | Atomic commit; race-loss returns False |
| CRD-20 – CRD-22 (history rows) | `tests/test_pipeline.py` | History appended; CI values present; before/after correct |
| CRD-25 – CRD-30 (sample builder) | `tests/learning/test_dataset.py` | Events loaded; conditions expanded; missing price skipped; abnormal flagging |
| CRD-31 – CRD-38 (estimator) | `tests/learning/test_estimator.py` | min_samples threshold; abnormal bypass; RESOLUTION inversion; weight scaling; NEUTRAL dropped |
| CRD-37 (seed writer) | `tests/learning/test_seed_writer.py` | upsert_conditioned_edge called for each estimate |
| End-to-end | `tests/test_integration.py` | Full PredictionScored → Neo4j edge update → Postgres commit |

---

## 12. Failure Handling

| Failure scenario | Behaviour |
|---|---|
| Duplicate `PredictionScored` (same prediction_id) | Idempotency check at start → skip; log `scored_duplicate_skipped` |
| Malformed edge_id in contributing_edges | `InvalidScoredMessageError`; message dead-lettered |
| Contributing edge not found in Neo4j | Log WARNING, skip that edge; remaining edges and sources still processed |
| Neo4j write succeeds but Postgres commit fails | CRITICAL log with prediction_id; exception re-raised; message re-queued; RISK: edge was already updated in Neo4j (see 13.2) |
| Concurrent delivery race (lost insert race) | `commit_updates` returns False; log `scored_duplicate_skipped`; no double-counting |
| `source_ids` is empty | Log WARNING; source updates skipped; edge updates continue |
| Neo4j unreachable during edge update | GraphTransportError raised; transaction not attempted; message re-queued |
| Offline learner: event has missing price | Sample skipped; counter logged at end |
| Offline learner: insufficient samples in group | Group skipped; no edge written |
| Offline learner: estimated direction is NEUTRAL | Edge not written; dropped silently |

---

## 13. Assumptions and Limitations

### 13.1 Accepted design decisions

| Decision | Rationale |
|---|---|
| Neo4j updated before Postgres commit | Neo4j has no rollback capability from the service layer; writing to Postgres last means the idempotency guard is set after all writes are committed, so a crash before the guard causes safe replay |
| Prior_floor = 1.0 (matches Beta(1,1) seed) | Prevents a seeded edge from being driven below the uninformed prior by early-phase noise; ensures reliability never drops below 1/(1+N) |
| Proportional credit for edges | Larger expert-weight edges have more influence on the prediction; they should receive more of the feedback signal |
| Equal credit for sources | There is no weighting signal for news sources in the current pipeline; equal credit is the least-biased assignment |
| Binary score only affects credibility | The score is 1.0/0.0; magnitude accuracy is not rewarded. This keeps the Beta-Bernoulli model simple |
| Offline learner reads cross-schema | The learner is a read-only analytics batch; it reads `cleansing` and `market_data` directly to avoid building a separate pipeline for historical events. It never writes to those schemas |
| Offline learner uses Beta(1,1) prior for new edges | Consistent with the expert-seeded prior; a data-derived edge starts with the same uninformed prior as a hand-seeded one |
| WEIGHT_RETURN_SCALE = 0.05 | A rough calibration: a 5% mean daily move maps to expert weight 1.0. Weights above 1.0 are capped, preventing extreme outliers from dominating |

### 13.2 Known limitations

- **Dual-write gap (Neo4j + Postgres):** the update is not a distributed transaction. If the Postgres commit fails after Neo4j writes succeed, the edge weights in Neo4j are permanently ahead of the Postgres audit trail. Manual replay using the logged `prediction_id` is required to re-commit the Postgres side. The Neo4j re-write would be a no-op (same values), but the credit-floor check would still fire, so the net outcome is correct with one extra Neo4j round-trip.
- **Offline learner reads `source_ids=[]`:** in the current pipeline, the Cleansing Service does not populate `source_ids` on the `EventDetected` message (it sets an empty list). Therefore `PredictionScored.source_ids` is also always empty, and source-credibility updates never accumulate real values. Source credibility tracking is structurally complete but data-starved.
- **Industry fan-out credit dilution:** if an event is assigned to 10 assets via industry fan-out, each asset's prediction gets a credit update. Because 10 independent predictions are scored, the same causal edge may receive 10 credit updates from one real-world event — one for each asset. This inflates both alpha and beta proportionally, so the effect on reliability is small for well-seeded edges but may cause drift for sparse ones.
- **The offline learner uses a fixed timezone (America/New_York)** for resolving sessions from `cleansing.events`; per-asset timezone resolution is used only when the asset is known to the registry. Unknown assets default to New York time, which is slightly wrong for European listings.
- **No confidence interval feedback to prediction policy:** the `ci_lower` and `ci_upper` stored in `credibility_history` are computed for observability but are not currently read by any other service. The Prediction Service uses only the edge reliability (`alpha / (alpha + beta)`).

---

## 14. How to Update This Document

### 14.1 When to update

Update this document whenever any of the following changes:

- The Beta-Bernoulli credit formula changes (proportional vs. equal, or a new scheme is added)
- The `prior_floor` semantics change
- The Wilson CI formula changes or a different interval is chosen
- The offline learner's estimation algorithm changes (deadband, weight scaling, abnormal detection)
- The `edge_id` business key format changes (adding a new form beyond unconditional/conditioned)
- Source credibility is wired up (when `source_ids` starts being populated)
- A new table column is added or modified in `db.py`
- An environment variable is added, removed, or has its default changed in `config.py` or `learning/config.py`
- The dual-write gap risk is mitigated (update section 13.2)
- A new test file is added (add it to section 11)

### 14.2 Steps to update

1. **Read the current source first** — verify behaviour before writing requirements
2. **Assign the next CRD-N ID** — check the highest existing ID and continue the sequence
3. **Update the relevant section**
4. **Add a row to section 15** (Change History) with date, what changed, and why
5. **Do not renumber existing IDs** — mark removed requirements as `Status: Withdrawn`
6. **Update `requirements/README.md`** if the ID range for CRD changes

---

## 15. Change History

| Date | Description |
|---|---|
| 2026-08-05 | Initial as-built specification for E07 (Credibility Service); CRD-1 through CRD-44 |
| 2026-08-07 | **Defect fix.** `parse_edge_id` coerced the `edge_id` target to `AssetId`, so every scored prediction that fired an *inherited group* edge was dead-lettered and its learning silently lost (8 such messages found in `credibility.scored.dlq`). ADR-007 and SyRS §9.2 always required both forms to parse; the code implemented only the asset form. CRD-11 reworded; CRD-45…CRD-47 added; §7.4 rewritten; §7.8 worked example added |
