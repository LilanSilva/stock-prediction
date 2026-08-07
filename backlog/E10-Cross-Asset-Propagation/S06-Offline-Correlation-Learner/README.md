# S06 — Offline Correlation Learner (`CORRELATES_WITH` edges)

## Overview

The existing offline structure learner (`credibility/learning/`) learns `CAUSES` edges by
reading historical `EventDetected` records and observed price moves. It cannot learn
`CORRELATES_WITH` edges because those edges have a different source (an `Asset`, not a
`CausalFactor`) and a different input signal (whether another asset's prediction was correct,
not whether a news event caused a move).

This story adds a second, parallel data path inside the same learner. Both paths run
sequentially inside the existing `run_with()` call in `run.py` — one APScheduler job fires,
both paths complete, the job ends. No scheduling changes are needed.

## What the correlation learner answers

> "When `XOM_NYSE` was predicted `UP` (direct, `propagation_depth=0`) and that prediction
> was scored, did `NEM_NYSE` actually move in the expected correlated direction that same
> settlement session?"

If yes consistently, the `CORRELATES_WITH(XOM_NYSE, UPSTREAM_UP, NEM_NYSE, DOWN)` edge
weight increases. If not, it decreases. If no edge exists yet but the data shows a reliable
pattern, the learner writes a new edge.

## Input tables (cross-schema reads — same exception as existing dataset.py)

| Table | Why |
|---|---|
| `prediction.predictions` | Source asset, direction, `propagation_depth`, `decision_at`, `status` |
| `prediction.outbox_events` | Join to recover the full `PredictionMade` payload including `propagation_chain` |
| `verification.scores` | `is_correct`, `actual_return`, `actual_direction`, `scored_at` per `prediction_id` |
| `market_data.close_observations` | Baseline and settlement closes for the **target** asset in the same session window |

Only **direct** predictions (`propagation_depth = 0`) are used as the upstream signal. A
propagated prediction cannot be the upstream signal — it is itself derived from another
asset's state, which would create circular learning.

## Idempotency

`build_correlation_samples` re-reads the full `lookback_days` window every run, exactly like
the existing `build_samples`. `write_correlation_estimates` calls `upsert_correlation_edge`
which uses `MERGE` — running it 10 times on the same data produces the same Neo4j state.

## Scheduling

No change. The existing APScheduler job (`structure_learning_sweep` in `app.py`) calls
`run_with()`, which is extended to call both paths sequentially. `max_instances=1` and
`coalesce=True` already prevent overlapping runs.

## Dependencies

- S01 (Neo4j `CORRELATES_WITH` edges) must be complete.
- S02 T01 (`PropagationHop` model, `UPSTREAM_UP`/`UPSTREAM_DOWN` condition codes) must be merged.
- S02 T03 (`upsert_correlation_edge` on `CausalGraphClient`) must be merged — see T04 below
  which may need to add this if S02 T03 only added `get_correlation_edges` and
  `update_correlation_weight` but not `upsert_correlation_edge`. Read `client.py` before
  starting T04.
- S03 must be complete so `prediction.predictions` contains rows with `propagation_depth=0`
  and scored outcomes in `verification.scores`.

## Tasks

| Task | Summary |
|---|---|
| [T01](T01-correlation-sample-model.md) | `CorrelationSample` and `CorrelationEdgeEstimate` models |
| [T02](T02-correlation-dataset.md) | `build_correlation_samples()` — reads prediction + score + price data |
| [T03](T03-correlation-estimator.md) | `estimate_correlation_edges()` — same Beta-Bernoulli math, new key shape |
| [T04](T04-correlation-seed-writer.md) | `write_correlation_estimates()` + `upsert_correlation_edge` on graph client |
| [T05](T05-run-wire-up.md) | Extend `run_with()` and `LearningSettings` to call both paths |

## How to test end-to-end

1. Seed `prediction.predictions` with direct (`propagation_depth=0`), scored predictions for
   `XOM_NYSE UP` where `NEM_NYSE` consistently moved `DOWN` in the same settlement session.
2. Run `python -m credibility.learning.run`.
3. Verify in Neo4j:

```cypher
MATCH (a1:Asset {id: 'XOM_NYSE'})-[r:CORRELATES_WITH {condition: 'UPSTREAM_UP'}]->
      (a2:Asset {id: 'NEM_NYSE'})
RETURN r.direction, r.weight, r.alpha, r.beta
```

Expected: `direction=DOWN`, `weight > 0`, `alpha > beta` (more agreements than disagreements).

4. Run the learner a second time with identical data — Neo4j state must be identical (idempotency).
5. With no qualifying rows in the lookback window, both `written_causes` and `written_corr`
   should be `0` and no exception raised.
