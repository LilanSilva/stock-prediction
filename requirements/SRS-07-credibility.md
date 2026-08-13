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
| Last updated | 2026-08-12 |
| Version | 1.2.0 |
| Replaces | `docs/functional-documents/credibility-service-functional-document.md` (deleted 2026-08-06) |
| Source code | `src/services/credibility/` |
| Config class | `credibility.config.CredibilitySettings`, `credibility.learning.config.LearningSettings` |
| DB schema | `credibility` (owned by this service; the offline learner reads `cleansing`, `market_data`, `prediction` and `verification` in read-only mode) |

---

## 2. Purpose and Scope

### 2.1 What this service does

The Credibility Service is the final stage in the feedback loop. It receives `PredictionScored` messages from the Verification Service and updates the causal knowledge graph so future predictions benefit from observed accuracy.

Specific responsibilities:

**Online consumer (always-on):**
- Consume `PredictionScored` messages
- Apply proportional outcome credit to each contributing CAUSES edge in Neo4j — for a *direct* prediction — by moving the edge's `weight` up on a correct prediction and down on a wrong one (CRD-7). Edge `alpha`/`beta` are left frozen (CRD-8a)
- When the scored prediction is a *propagated* one (`propagation_chain` is non-empty), apply the same proportional weight credit instead to **every** `CORRELATES_WITH` edge listed in `contributing_edges` — that is, every contributing edge whose `edge_id` has the correlation form `SOURCE_ASSET|UPSTREAM_UP->TARGET_ASSET` (or `UPSTREAM_DOWN`). A target that two upstream assets converged on has more than one contributing correlation edge, so each edge earns a share of one observation proportional to its `influence_weight`; a single-edge propagation still earns the full 1.0. The CAUSES edges in the chain already earned their credit from the direct prediction that started it, so they are not credited again
- Apply equal Beta-Bernoulli credit to each contributing news source in PostgreSQL
- Write an immutable history row per updated entity for auditing and 95% confidence-interval tracking
- Ignore a scored prediction that was superseded (`status = 'WITHDRAWN'`): it never stood as the asset's stance, so its outcome carries no information about the edges that produced it (CRD-8b)
- Guard idempotency: each `prediction_id` is processed at most once

**Offline structure learner (scheduled or CLI):**
- Mine historical events and price outcomes from the database
- Estimate (factor, condition, asset) edge parameters from aggregated sample statistics
- **Create** conditioned CAUSES edges in Neo4j with data-derived direction, weight, and Beta-Bernoulli counts. An edge that already exists is never modified (CRD-37): the learner grows the graph's coverage, it does not revise established structure
- Run a **second data path in the same batch** for `CORRELATES_WITH` edges: read historical *direct* scored predictions (`propagation_depth = 0`) from `prediction.predictions` joined to `verification.evaluations`/`verification.scores`, derive the upstream condition from the predicted direction, measure each correlated target asset's actual return over the same baseline/settlement sessions, and create `CORRELATES_WITH` edges via MERGE with `ON CREATE SET` — discovering new ones where the data supports them, while leaving expert-seeded and outcome-learned edges untouched

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
| Abnormal sample | A sample where `|actual_return| >= abnormal_threshold × historical_volatility`; lowers the evidence bar from `min_samples` to `abnormal_min_samples` |
| EdgeEstimate | Estimated direction, weight, confidence, alpha, beta for one (factor, condition, asset) group |
| WEIGHT_RETURN_SCALE | 0.05 (5%); a 5% mean signed daily move maps to expert weight = 1.0 |
| CORRELATES_WITH edge learning | Outcome-driven update of the `(:Asset)-[:CORRELATES_WITH]->(:Asset)` edge `weight`; performed online when a *propagated* prediction is scored. The offline correlation path only *creates* such edges, never revises them |
| propagation_chain | The ordered list of `PropagationHop` values carried on `PredictionMade` and forwarded on `PredictionScored`; each hop records the provenance of one `CORRELATES_WITH` edge that contributed to the prediction, plus its depth. Empty for a direct prediction. It **marks** a message as propagated; it does not select which edge is credited |
| Converged target | A target asset reached by more than one inbound `CORRELATES_WITH` edge at the same propagation depth. Prediction sums the forces and decides once, then emits one `PropagationHop` *per* contributing edge — so `propagation_chain` can hold several hops sharing one `target_asset_id`, and "the last hop" is not a meaningful selector |
| Correlation-form edge_id | A `contributing_edges` `edge_id` of the form `"SOURCE_ASSET\|UPSTREAM_UP->TARGET_ASSET"` or `"SOURCE_ASSET\|UPSTREAM_DOWN->TARGET_ASSET"`. Only these two conditions identify a `CORRELATES_WITH` edge; every other condition prefix belongs to a CAUSES edge |
| Upstream condition | `UPSTREAM_UP` when the source asset was predicted UP, `UPSTREAM_DOWN` when DOWN; a `CORRELATES_WITH` edge is always conditioned on one of the two |
| CorrelationSample | One offline observation: (source_asset, upstream condition, target_asset) → the target's actual return over the source prediction's own baseline/settlement sessions |
| CorrelationEdgeEstimate | Estimated direction, weight, confidence, alpha, beta for one (source_asset, condition, target_asset) group |
| Correlation edge entity_id | Business key format for a correlation-edge history row: `"SOURCE_ASSET\|CONDITION->TARGET_ASSET"` (`entity_type = "edge"`) |

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
  - propagation_chain empty  → proportional credit → each contributing CAUSES edge in Neo4j
  - propagation_chain present → proportional credit → each correlation-form edge in
                                contributing_edges → CORRELATES_WITH edges in Neo4j
  - equal credit → each source in PostgreSQL
  - commit Postgres (idempotency guard + upserts + history)
       |
       ├── writes → Neo4j (CAUSES or CORRELATES_WITH edge weight)
       └── writes → PostgreSQL (credibility.credibility upsert + credibility_history append)

[Offline structure learner]  ← runs on schedule or via CLI, two data paths in one pass
  - path 1 (CAUSES):          reads cleansing.events + market_data.close_observations
  - path 2 (CORRELATES_WITH): reads prediction.predictions + prediction.outbox_events
                              + verification.evaluations + verification.scores
                              + market_data.close_observations, and the existing
                              CORRELATES_WITH edges from Neo4j
  - estimates edge parameters from historical data
  - writes conditioned CAUSES edges and CORRELATES_WITH edges → Neo4j (MERGE)
```

- Consumes from: `credibility.scored`
- No outbound messages (no outbox)
- Writes: Neo4j (CAUSES and CORRELATES_WITH edge weight updates), PostgreSQL (credibility + credibility_history tables)
- The offline learner: reads `cleansing.events`, `market_data.close_observations`, `prediction.*` and `verification.*` (read-only cross-schema), writes Neo4j conditioned CAUSES edges and CORRELATES_WITH edges

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
| CRD-6 | For each edge, the service shall read the current `(alpha, beta, weight)` from Neo4j by matching on the full `edge_id` business key | Implemented |
| CRD-7 | If `is_correct = True`: `weight += weight_step × credit`; if `is_correct = False`: `weight -= weight_step × credit` | Implemented |
| CRD-8 | The new `weight` shall be clamped to `[weight_floor, 1.0]` (defaults 0.05 and 1.0), so sustained bad outcomes make an edge negligible rather than deleting it or letting it change sign | Implemented |
| CRD-8a | An edge's `alpha` and `beta` shall **not** be modified by a scored prediction. Reliability cancels out of the decision's net/total ratio whenever a single edge fires — 92% of predictions — so counting outcomes there had no observable effect; `weight` is what feeds magnitude | Implemented |
| CRD-8b | A scored prediction whose `prediction.status = 'WITHDRAWN'` shall be ignored: no edge or source weight shall change. It shall still be recorded in `processed_predictions` so redelivery does not re-check. A superseded prediction never stood as the asset's stance, so its outcome says nothing about the edges that produced it | Implemented |
| CRD-9 | The new `weight` shall be written back to Neo4j via the shared graph client's `update_edge_weight` | Implemented |
| CRD-10 | If a contributing edge is not found in Neo4j (the graph was modified after the prediction), a WARNING shall be logged and that edge skipped; remaining edges still proceed | Implemented |
| CRD-11 | The `edge_id` parser shall accept `"FACTOR->TARGET"` (unconditional) and `"FACTOR|CONDITION->TARGET"` (conditioned), where `TARGET` is either a registry asset ID or an industry **group** ID; any other format shall raise `InvalidScoredMessageError` (terminal) | Implemented |
| CRD-45 | A `TARGET` that is neither a declared asset nor a declared asset group shall raise `InvalidScoredMessageError` (terminal) | Implemented |
| CRD-46 | When `TARGET` is a group ID, the service shall read and update the `(:CausalFactor)-[:CAUSES]->(:AssetGroup)` edge itself, **not** a per-asset edge — an inherited edge's credit belongs to the industry prior that fired | Implemented |
| CRD-47 | The current `(alpha, beta, weight)` for a group edge shall be read by matching `(factor, group, condition)` directly, never through a member asset | Implemented |
| CRD-48 | When the `PredictionScored` carries a non-empty `propagation_chain`, the service shall apply credit to the `CORRELATES_WITH` edges named by `contributing_edges`: every contributing edge whose `edge_id` parses as a correlation-form id, and only those. `propagation_chain` marks the message as propagated; it does not select the edge to credit | Implemented |
| CRD-49 | The credit for those correlation edges shall be computed with the same `compute_proportional_credits(...)` helper used for CAUSES edges, keyed on each edge's `influence_weight`: the shares sum to one full observation (`1.0`), so two edges converging on one target each receive a proportional part and a single-edge propagation still receives the full `1.0`. The credit then moves `weight` per CRD-7/CRD-8, not `alpha`/`beta` | Implemented |
| CRD-50 | The current `(alpha, beta, weight)` for a correlation edge shall be read via `get_correlation_edge_counts(source_asset_id, target_asset_id, condition)` and the new `weight` written back via `update_correlation_weight` | Implemented |
| CRD-51 | If a contributing correlation edge is absent from Neo4j, a WARNING `correlation_edge_missing_in_graph` shall be logged and that edge's update skipped; this is not fatal — the remaining correlation edges, the source updates, and the Postgres commit still proceed | Implemented |
| CRD-52 | When the `PredictionScored` carries an empty `propagation_chain` (a direct prediction), the CAUSES path of `CRD-4` – `CRD-11` shall run unchanged and no `CORRELATES_WITH` edge shall be updated. The two paths are mutually exclusive: a propagated prediction never updates a CAUSES edge, because the CAUSES edges upstream of it already earned credit from the direct prediction that started the chain | Implemented |
| CRD-64 | `parse_correlation_edge_id(edge_id)` shall return `(source_asset_id, condition, target_asset_id)` for the correlation form `"SOURCE_ASSET\|UPSTREAM_UP->TARGET_ASSET"` / `"…\|UPSTREAM_DOWN->…"`, and shall return `None` for any other form — notably every CAUSES form — so the caller falls through to `parse_edge_id`. Only a correlation-form id that names an asset unknown to the registry shall raise `InvalidScoredMessageError` (terminal) | Implemented |
| CRD-65 | If a propagated prediction's `contributing_edges` contains no correlation-form `edge_id` at all, a WARNING `propagated_prediction_without_correlation_edges` shall be logged with the `prediction_id` and the offending `edge_ids`, and no edge update shall be made; crediting a CAUSES edge for a propagated prediction is never correct. This is not expected to occur in practice | Implemented |

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
| CRD-20 | For each updated entity, the service shall append one row to `credibility_history` with `alpha_before`, `beta_before`, `alpha_after`, `beta_after`, `credibility_before`, `credibility_after`, `ci_lower`, and `ci_upper`, plus `weight_before` and `weight_after` for an `edge` entity (NULL for a `source`, which is still alpha/beta-driven) | Implemented |
| CRD-21 | `ci_lower` and `ci_upper` shall be the 95% Wilson score confidence interval for the Bernoulli proportion `alpha / (alpha + beta)` | Implemented |
| CRD-22 | The `credibility_history` table is append-only and never modified; it provides a complete audit trail of every credibility change | Implemented |
| CRD-53 | A correlation-edge update shall be recorded with `entity_id = "SOURCE_ASSET\|CONDITION->TARGET_ASSET"` and `entity_type = "edge"`, so correlation and causal edges share one audit trail and one current-state table | Implemented |

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
| CRD-31 | Groups with fewer than `min_samples` (default 5) observations shall be dropped, unless any sample in the group is flagged abnormal, in which case the minimum is `abnormal_min_samples` (default 2). It is not lowered to 1: the learner only creates edges, but an edge conjured from a single observation is too thin to act on | Implemented |
| CRD-32 | Direction: UP when `mean_signed_return > deadband`; DOWN when `< -deadband`; NEUTRAL otherwise (NEUTRAL edges are dropped, not written) | Implemented |
| CRD-33 | `weight = min(1.0, abs(mean_signed_return) / 0.05)` — a 5% mean move maps to weight 1.0 | Implemented |
| CRD-34 | `confidence = agreeing_count / total_samples` where agreeing = samples with return matching the estimated direction | Implemented |
| CRD-35 | `alpha = agreeing + 1.0` (prior); `beta = disagreeing + 1.0` (prior) | Implemented |
| CRD-36 | RESOLUTION-polarity samples are inverted before aggregation: `signed_return = -actual_return` for RESOLUTION, `+actual_return` for OCCURRENCE | Implemented |
| CRD-37 | Estimates shall be written to Neo4j using `upsert_conditioned_edge` with **`ON CREATE SET`** semantics: the edge is created when absent, and an edge that already exists shall be left entirely unchanged — `direction`, `weight`, `confidence`, `alpha` and `beta` are all preserved. The offline learner discovers new causal structure; it never revises existing structure. A bare `SET` previously let one batch run overwrite an expert-seeded edge (and the outcome-driven weight) from a handful of samples, in one case flipping its direction | Implemented |
| CRD-38 | NEUTRAL estimated edges are not written to Neo4j (no neutral CAUSES edges in the graph) | Implemented |

### 5.7 Offline structure learner: CORRELATES_WITH path

This is a second data path inside the existing learner, not a separate service or process. One
`run_with()` pass performs the CAUSES path first, then this one, and reports the sum of edges written.

| ID | Requirement | Status |
|---|---|---|
| CRD-54 | The correlation path shall run in the same learning pass as the CAUSES path and shall be skipped when `CREDIBILITY_LEARNING_CORRELATION_LEARNING_ENABLED=false` (default `true`); the pass shall return the total number of edges written by both paths | Implemented |
| CRD-55 | The correlation sample builder shall read only **direct** scored predictions: `propagation_depth = 0` (read from the prediction outbox payload) and `direction != 'NEUTRAL'`, joining `prediction.predictions`, `prediction.outbox_events`, `verification.evaluations`, and `verification.scores`, restricted to predictions decided within the last `lookback_days` days | Implemented |
| CRD-56 | For each such prediction the condition shall be derived from the predicted direction: UP → `UPSTREAM_UP`, DOWN → `UPSTREAM_DOWN` | Implemented |
| CRD-57 | For each `CORRELATES_WITH` target of that (source asset, condition) pair, the learner shall compute the target's `actual_return` over the **same** baseline and settlement sessions as the source prediction; a target with a missing close on either session shall be skipped, and a target unknown to the asset registry shall be skipped with a WARNING | Implemented |
| CRD-58 | A `CorrelationSample` shall be flagged `is_abnormal=True` when `abs(actual_return) >= abnormal_threshold × target_volatility`, using the same volatility window as the CAUSES path | Implemented |
| CRD-59 | The correlation estimator shall group samples by `(source_asset, condition, target_asset)` and shall **not** invert the return sign for polarity — unlike `CRD-36`, the condition already encodes the upstream direction | Implemented |
| CRD-60 | Correlation direction shall be UP when the mean `actual_return > deadband`, DOWN when `< -deadband`, NEUTRAL otherwise; `weight = min(1.0, abs(mean) / 0.05)`, `confidence = agreeing / total`, `alpha = agreeing + 1.0`, `beta = disagreeing + 1.0` | Implemented |
| CRD-61 | A correlation group with fewer than `min_samples` observations shall be dropped, unless any sample in the group is flagged abnormal, in which case the effective minimum is `abnormal_min_samples` (default 2) | Implemented |
| CRD-62 | NEUTRAL correlation estimates shall not be written; every other estimate shall be written with `upsert_correlation_edge` (idempotent MERGE), so re-running the batch refines existing edges rather than duplicating them | Implemented |

### 5.8 Health and readiness

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
| CRD-63 | The correlation path of the offline learner shall likewise be deterministic: the same correlation samples shall yield the same `CorrelationEdgeEstimate` values, sorted by `(source_asset, condition, target_asset)` | Implemented |

---

## 7. How It Works

### 7.1 Online consumer pipeline

```
[PredictionScored arrives]
  ↓
CRD-2: Check processed_predictions — if found, skip (return False)
  ↓
_update_edges(message) — branches on propagation_chain
  ↓
CRD-48–CRD-52, CRD-64–CRD-65: propagated prediction (propagation_chain non-empty)
                              — CORRELATES_WITH branch, _update_correlation_edges(message)
  corr_edges = [e for e in message.contributing_edges
                  if parse_correlation_edge_id(e.edge_id) is not None]
  if not corr_edges:                                                     # CRD-65
      log propagated_prediction_without_correlation_edges(prediction_id, edge_ids)
      return []   ← no edge updates
  credits = compute_proportional_credits(corr_edges)   # same helper as the CAUSES path
  For each edge in corr_edges:
    source, condition, target = parse_correlation_edge_id(edge.edge_id)
    alpha, beta = graph.get_correlation_edge_counts(source, target, condition)
    if missing: log correlation_edge_missing_in_graph, skip this edge (not fatal)
    alpha_after, beta_after = apply_bernoulli(alpha, beta, credits[edge.edge_id],
                                             is_correct, floor)
    graph.update_correlation_weight(source, target, condition, alpha_after, beta_after)
    record WeightUpdate(entity_id="SOURCE|CONDITION->TARGET", entity_type="edge")
  return  ← the CAUSES branch below is not entered
  ↓
CRD-4–CRD-10: direct prediction (propagation_chain empty) — CAUSES branch, unchanged
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

This is **path 1** of the pass (CAUSES edges). Path 2 (`CORRELATES_WITH`) is described in §7.10 and
runs immediately afterwards in the same `run_with()` call.

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
    if count < (abnormal_min_samples if any abnormal else min_samples): skip
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

### 7.9 Worked example (propagated prediction → CORRELATES_WITH edges)

**Input:** `PredictionScored` for `NEM_NYSE`, `is_correct=True`,
`contributing_edges=[ContributingEdge(edge_id="XOM_NYSE|UPSTREAM_UP->NEM_NYSE",
influence_weight=1.0)]`, `propagation_chain=[PropagationHop(source_asset_id=XOM_NYSE,
target_asset_id=NEM_NYSE, condition=UPSTREAM_UP, direction=DOWN, edge_weight=0.45)]`.

1. `processed_predictions` check → not found → proceed
2. `propagation_chain` is non-empty → the CAUSES path is not entered at all (`CRD-48`, `CRD-52`)
3. `contributing_edges` filtered by `parse_correlation_edge_id` → one correlation edge,
   `(XOM_NYSE, UPSTREAM_UP, NEM_NYSE)` (`CRD-64`)
4. `compute_proportional_credits(...)` over that single edge → credit = 1.0 (`CRD-49`)
5. `graph.get_correlation_edge_counts(XOM_NYSE, NEM_NYSE, UPSTREAM_UP)` → alpha=2.0, beta=1.0
6. `apply_bernoulli(2.0, 1.0, 1.0, is_correct=True, floor=1.0)` → alpha=3.0, beta=1.0 — the full
   observation, because this one edge produced the whole prediction
7. `graph.update_correlation_weight(XOM_NYSE, NEM_NYSE, UPSTREAM_UP, alpha=3.0, beta=1.0)`
8. Postgres commit: upsert `credibility("XOM_NYSE|UPSTREAM_UP->NEM_NYSE", edge, 3.0, 1.0, 0.75)` and
   append the history row (`CRD-53`)

If step 5 returns `None` (the edge was removed from the graph after the prediction was made), a
WARNING `correlation_edge_missing_in_graph` is logged, no edge update is recorded for that edge, and
processing continues to the source updates and the Postgres commit (`CRD-51`).

**Second case — a converged target (two contributing correlation edges).**

**Input:** `PredictionScored` for `SWED_A_STO`, `is_correct=True`, `contributing_edges=`
[`NEM_NYSE|UPSTREAM_DOWN->SWED_A_STO` (influence_weight=0.90),
`LUG_STO|UPSTREAM_DOWN->SWED_A_STO` (influence_weight=0.20)], with one `PropagationHop` per
contributing edge — both hops naming the same `target_asset_id`.

1. `processed_predictions` check → not found → proceed
2. `propagation_chain` is non-empty → CORRELATES_WITH branch (`CRD-48`)
3. Both `edge_id`s parse as correlation form → both are credited (`CRD-64`)
4. `compute_proportional_credits(...)`: total = 1.10 → `NEM_NYSE` credit = 0.818,
   `LUG_STO` credit = 0.182; the two sum to one full observation (`CRD-49`)
5. Each edge starting at alpha=1.0, beta=1.0 → `NEM_NYSE` edge becomes alpha=1.818,
   `LUG_STO` edge becomes alpha=1.182; both betas stay 1.0
6. Two `update_correlation_weight` calls, two `WeightUpdate` rows, two history rows (`CRD-53`)

Had the prediction been wrong, the same two credits would have landed on `beta` instead — the
heavier edge takes the larger share of the blame. Crediting only one of the two (the old "last hop"
rule) would have given that edge a full observation it did not earn alone, while the other edge
learned nothing.

### 7.10 Offline structure learner: CORRELATES_WITH path

**Path 2** of the same `run_with()` pass, gated by `correlation_learning_enabled`. Note that this path
learns from **direct** predictions only: it asks "when `XOM_NYSE` was predicted UP, what did the
correlated assets actually do?", which is an unbiased question. Learning from propagated predictions
instead would feed the learner its own prior output.

```
Step 1 — build_correlation_samples(pool, graph, lookback_days, volatility_lookback_days,
                                   abnormal_threshold)
  SELECT direct scored predictions decided within the lookback window:
      prediction.predictions p
      JOIN prediction.outbox_events   o ON o.aggregate_id = p.prediction_id
      JOIN verification.evaluations   e USING (prediction_id)   -- baseline + settlement sessions
      JOIN verification.scores        s USING (prediction_id)   -- scored only
      WHERE p.direction != 'NEUTRAL'
        AND (o.payload::jsonb ->> 'propagation_depth')::int = 0
  For each prediction row:
    condition = UPSTREAM_UP if predicted_direction == UP else UPSTREAM_DOWN
    for each target in graph.get_correlation_edges(source_asset, condition):
      if target unknown to the registry: log warning, skip
      baseline_close, settlement_close = closes for TARGET on the SOURCE prediction's sessions
      if either missing: skip
      actual_return = (settlement_close - baseline_close) / baseline_close
      volatility = std_dev(target's daily returns over the volatility window)
      is_abnormal = (volatility > 0 AND |actual_return| >= abnormal_threshold × volatility)
      emit CorrelationSample(source_asset, condition, target_asset, actual_return, ...)

Step 2 — estimate_correlation_edges(samples, deadband=0.002, min_samples=5)
  Group by (source_asset, condition, target_asset)
  For each group:
    if count < min_samples AND no abnormal samples: skip
    mean = average(actual_return)          # NO polarity sign-flip: the condition carries direction
    if mean >  deadband: direction=UP,   agreeing = count(return > 0)
    elif mean < -deadband: direction=DOWN, agreeing = count(return < 0)
    else: direction=NEUTRAL
    weight = min(1.0, |mean| / 0.05); confidence = agreeing / total
    alpha = agreeing + 1.0; beta = (total - agreeing) + 1.0
  Sort by (source_asset, condition, target_asset)

Step 3 — write_correlation_estimates(graph, estimates)
  For each estimate with direction != NEUTRAL:
    graph.upsert_correlation_edge(source_asset, condition, target_asset,
        direction=..., weight=..., confidence=..., alpha=..., beta=...)
  Returns count of edges written

Step 4 — run_with returns written_causes + written_corr and logs learning_run_complete
```

---

## 8. Interfaces

### 8.1 Consumed message

**Queue:** `credibility.scored`  
**Type:** `PredictionScored` (see SRS-01, section 5.9)

Key fields used:
- `prediction_id` — idempotency key
- `is_correct` — determines whether credit goes to alpha or beta
- `contributing_edges` — list of `ContributingEdge`; `edge_id` and `influence_weight` used. This is the source of truth for which edges fired on **both** paths: CAUSES edge ids for a direct prediction, correlation-form edge ids for a propagated one
- `source_ids` — list of news source domain strings (equal credit)
- `propagation_chain` — list of `PropagationHop` forwarded from `PredictionMade`; empty for a direct prediction. A non-empty chain only **marks** the message as propagated (and carries provenance/depth), routing credit to the correlation-form entries of `contributing_edges`; it does not itself select an edge. A converged target contributes one hop per inbound edge, so several hops may share one `target_asset_id`

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
| `offline_learner` | `learning_interval_hours` (default 24 h) | Runs one build → estimate → write pass over both edge paths (CAUSES, then CORRELATES_WITH); only if `learning_enabled=true` |

### 8.5 CLI

| Command | What it does |
|---|---|
| `python -m credibility.learning.run` | Runs one offline learning pass (both edge paths) using the current database state |

---

## 9. Data Design

### 9.1 Table: `credibility.credibility`

| Column | Type | Notes |
|---|---|---|
| `entity_id` | TEXT NOT NULL | `"FACTOR->ASSET"`, `"FACTOR|COND->ASSET"`, `"SOURCE_ASSET\|COND->TARGET_ASSET"` (correlation edge), or a domain string |
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
| `CREDIBILITY_LEARNING_LOOKBACK_DAYS` | `30` | How far back to look — in `cleansing.events` for the CAUSES path, and in `prediction.predictions.decision_at` for the CORRELATES_WITH path |
| `CREDIBILITY_LEARNING_VOLATILITY_LOOKBACK_DAYS` | `30` | Days of closes used to compute per-asset historical volatility |
| `CREDIBILITY_LEARNING_ABNORMAL_THRESHOLD` | `2.0` | Multiplier: `|return| >= threshold × volatility` = abnormal |
| `CREDIBILITY_LEARNING_DB_POOL_MIN_SIZE` | `1` | asyncpg minimum pool connections |
| `CREDIBILITY_LEARNING_DB_POOL_MAX_SIZE` | `5` | asyncpg maximum pool connections |
| `CREDIBILITY_LEARNING_CORRELATION_LEARNING_ENABLED` | `true` | Whether the `CORRELATES_WITH` path runs after the CAUSES path in the same pass; set `false` to run only the CAUSES path |

`deadband`, `min_samples`, `lookback_days`, `volatility_lookback_days` and `abnormal_threshold` are
shared by both learning paths; there is no separate correlation-only tuning knob.

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
| CRD-48, CRD-50 – CRD-51, CRD-53 (online correlation credit) | `tests/test_pipeline.py` | `test_correct_propagated_prediction_increments_alpha`; `test_wrong_propagated_prediction_increments_beta`; `test_propagated_prediction_missing_corr_edge_returns_no_update` — missing edge is skipped, `process` still returns True |
| CRD-49 (proportional split across converging edges) | `tests/test_pipeline.py` | `test_converging_edges_receive_proportional_credit` — both edges credited, heavier `influence_weight` gets more, credits sum to 1.0; `test_converging_edges_share_the_blame_when_wrong` — same split lands on `beta`; `test_single_edge_propagation_still_gets_full_credit` — the one-edge case is unchanged at a full 1.0 |
| CRD-52 (path exclusivity) | `tests/test_pipeline.py` | `test_direct_prediction_does_not_call_update_correlation_weight` — an empty `propagation_chain` leaves the CORRELATES_WITH graph untouched |
| CRD-64 (correlation edge_id parser) | `tests/test_pipeline.py` | `test_parse_correlation_edge_id_recognises_correlation_form` — `UPSTREAM_*` form returns `(source, condition, target)`; `test_parse_correlation_edge_id_returns_none_for_causes_edges` — unconditional and conditioned CAUSES ids return `None` so they fall through to `parse_edge_id` |
| CRD-54 (dual-path pass + flag) | `tests/test_learning_run.py` | `test_learning_settings_correlation_learning_enabled_default_true`; `test_learning_settings_correlation_learning_can_be_disabled`; `test_run_with_empty_data_returns_zero`; `test_run_with_skips_corr_path_when_disabled` |
| CRD-55 – CRD-58 (correlation sample builder) | `tests/test_learning_dataset.py` | `test_build_correlation_samples_empty_when_no_prediction_rows`; `..._skips_when_no_corr_edges`; `..._produces_sample`; `..._skips_target_with_missing_price` |
| CRD-59 – CRD-61, CRD-63 (correlation estimator, incl. determinism) | `tests/test_learning_estimator.py` | `test_corr_positive_returns_yield_up_edge`; `..._negative_returns_yield_down_edge`; `..._neutral_group_is_not_dropped_but_returns_neutral_direction`; `..._below_min_samples_is_dropped`; `..._single_abnormal_sample_bypasses_min_samples`; `..._groups_split_by_source_condition_target`; `..._output_sorted_deterministically` |
| CRD-62 (correlation seed writer) | `tests/test_learning_seed_writer.py` | `test_write_correlation_estimates_skips_neutral`; `..._calls_upsert_for_directional`; `..._empty_input_returns_zero` |
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
| Contributing correlation edge not found in Neo4j | Log WARNING `correlation_edge_missing_in_graph`; no edge update recorded for that edge; the other contributing correlation edges, the sources, and the Postgres commit still proceed (`process` returns True) |
| Propagated prediction whose `contributing_edges` holds no correlation-form edge_id | Log WARNING `propagated_prediction_without_correlation_edges` with the `prediction_id` and `edge_ids`; no edge updates; sources and the Postgres commit still proceed. Not expected in practice (`CRD-65`) |
| Correlation-form edge_id naming an asset unknown to the registry | `InvalidScoredMessageError`; message dead-lettered (`CRD-64`) |
| Offline learner: event has missing price | Sample skipped; counter logged at end |
| Offline learner: insufficient samples in group | Group skipped; no edge written |
| Offline learner: estimated direction is NEUTRAL | Edge not written; dropped silently |
| Offline learner: source asset has no CORRELATES_WITH edges | Prediction contributes no correlation samples; skipped silently |
| Offline learner: correlation target missing a close on either session | That target's sample skipped; `skipped_missing_price` counter logged at end |

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
| Offline learner reads cross-schema | The learner is a read-only analytics batch; it reads `cleansing`, `market_data`, `prediction` and `verification` directly to avoid building a separate pipeline for historical events and outcomes. It never writes to those schemas |
| Proportional credit across contributing correlation edges, reusing `compute_proportional_credits` | A propagated prediction is not always produced by a single edge: Prediction sums the inbound forces at a converged target and decides once, so several `CORRELATES_WITH` edges can be jointly responsible for one outcome. Splitting by `influence_weight` keeps one scored prediction worth exactly one observation and applies the same rule already used for CAUSES edges, rather than a second credit scheme. The credit for the CAUSES edges further up the chain was already applied when the direct prediction that started the chain was scored |
| `contributing_edges`, not `propagation_chain`, decides which edge is credited | `contributing_edges` is what `decide()` actually reported, so it is the authoritative record of which edges fired. `propagation_chain` is provenance: it marks the message as propagated and carries depth, and for a converged target it holds one hop per inbound edge — so no single hop ("the last one") identifies the edge that produced the prediction |
| Correlation learning uses only direct predictions (`propagation_depth = 0`) | Learning from propagated predictions would feed the learner its own prior output, reinforcing whatever the seeded correlation weights already claimed. A direct prediction plus the target's realised return is an independent observation |
| Correlation learning does not sign-flip for polarity | The condition (`UPSTREAM_UP` / `UPSTREAM_DOWN`) already encodes the upstream direction, so the target's raw return is already in the correct orientation — unlike `CRD-36`, where RESOLUTION events must be inverted |
| Correlation learning behind its own flag | `CREDIBILITY_LEARNING_CORRELATION_LEARNING_ENABLED=false` restores the previous single-path behaviour without a code change, so the new path can be disabled during rollout or debugging |
| Offline learner uses Beta(1,1) prior for new edges | Consistent with the expert-seeded prior; a data-derived edge starts with the same uninformed prior as a hand-seeded one |
| WEIGHT_RETURN_SCALE = 0.05 | A rough calibration: a 5% mean daily move maps to expert weight 1.0. Weights above 1.0 are capped, preventing extreme outliers from dominating |

### 13.2 Known limitations

- **Dual-write gap (Neo4j + Postgres):** the update is not a distributed transaction. If the Postgres commit fails after Neo4j writes succeed, the edge weights in Neo4j are permanently ahead of the Postgres audit trail. Manual replay using the logged `prediction_id` is required to re-commit the Postgres side. The Neo4j re-write would be a no-op (same values), but the credit-floor check would still fire, so the net outcome is correct with one extra Neo4j round-trip.
- **Offline learner reads `source_ids=[]`:** in the current pipeline, the Cleansing Service does not populate `source_ids` on the `EventDetected` message (it sets an empty list). Therefore `PredictionScored.source_ids` is also always empty, and source-credibility updates never accumulate real values. Source credibility tracking is structurally complete but data-starved.
- **Industry fan-out credit dilution:** if an event is assigned to 10 assets via industry fan-out, each asset's prediction gets a credit update. Because 10 independent predictions are scored, the same causal edge may receive 10 credit updates from one real-world event — one for each asset. This inflates both alpha and beta proportionally, so the effect on reliability is small for well-seeded edges but may cause drift for sparse ones.
- **The offline learner uses a fixed timezone (America/New_York)** for resolving sessions from `cleansing.events`; per-asset timezone resolution is used only when the asset is known to the registry. Unknown assets default to New York time, which is slightly wrong for European listings.
- **No confidence interval feedback to prediction policy:** the `ci_lower` and `ci_upper` stored in `credibility_history` are computed for observability but are not currently read by any other service. The Prediction Service uses only the edge reliability (`alpha / (alpha + beta)`).
- **Only the edges that fired at the scored prediction's own depth are credited:** upstream hops of a chain longer than one are not credited by the scored prediction at the far end. This is correct rather than lossy in the current pipeline, because every depth level's prediction is emitted and scored as its own `PredictionMade`, so each edge is credited when *its own* prediction is scored — and a converged target credits all of its inbound edges. If propagation ever emitted a single terminal prediction for a multi-hop chain, the upstream edges would silently stop learning.
- **Correlation and causal edges share one `entity_id` namespace:** a correlation edge is recorded as `"SOURCE_ASSET|CONDITION->TARGET_ASSET"` and a conditioned causal edge as `"FACTOR|CONDITION->ASSET"`, both with `entity_type = "edge"`. The two cannot collide today because an `EventType` value is never an `AssetId` value, but nothing in the schema enforces that separation.
- **Correlation learning is data-starved until predictions accumulate:** the CORRELATES_WITH path needs `min_samples` (default 5) scored direct predictions for the *same* (source asset, condition, target asset) triple within `lookback_days`. Early in a deployment it typically writes nothing, and expert-seeded correlation weights remain in force.

---

## 14. How to Update This Document

### 14.1 When to update

Update this document whenever any of the following changes:

- The Beta-Bernoulli credit formula changes (proportional vs. equal, or a new scheme is added)
- The `prior_floor` semantics change
- The Wilson CI formula changes or a different interval is chosen
- The offline learner's estimation algorithm changes (deadband, weight scaling, abnormal detection), on either the CAUSES or the CORRELATES_WITH path
- The `edge_id` business key format changes (adding a new form beyond unconditional/conditioned/correlation)
- The rule for selecting which correlation edges are credited changes, or propagation stops emitting one scored prediction per depth level
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
| 2026-08-12 | **E10 (Cross-Asset Propagation), version 1.1.0.** `CORRELATES_WITH` edges now learn on two paths. Online: a scored prediction with a non-empty `propagation_chain` credits the last hop's correlation edge with full 1.0 credit instead of the CAUSES edges (CRD-48…CRD-53, §7.9). Offline: the existing structure learner gained a second data path that estimates correlation edges from scored *direct* predictions and their targets' realised returns, gated by `CREDIBILITY_LEARNING_CORRELATION_LEARNING_ENABLED` (CRD-54…CRD-63, §5.7, §7.10). §2.1, §3, §4, §8.1, §8.4, §8.5, §10.2, §11, §12, §13 updated; former §5.7 (health and readiness) renumbered to §5.8. No existing requirement changed or withdrawn |
| 2026-08-12 | **Defect fix, version 1.2.0.** E10's design promised force summation for conflicting propagated forces, but the pipeline decided each target from a single edge, so the online consumer credited only the chain's last hop with a full 1.0. Prediction was fixed to collect all inbound correlation edges per depth level and decide once, so a converged target can legitimately have several contributing correlation edges — and it now emits one `PropagationHop` per contributing edge, making "the last hop" meaningless as a selector. `_update_edges` still branches on a non-empty `propagation_chain` but now calls `_update_correlation_edges`, which filters `contributing_edges` through the new `parse_correlation_edge_id` and splits credit with the existing `compute_proportional_credits` — the same helper the CAUSES path uses. CRD-48, CRD-49, CRD-51 and CRD-52 reworded; CRD-64 (correlation edge_id parser) and CRD-65 (`propagated_prediction_without_correlation_edges` warning) added; §2.1, §3, §4, §7.1, §7.9, §8.1, §11, §12, §13, §14.1 updated. No requirement withdrawn |
