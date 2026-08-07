# E10 — Cross-Asset Causal Propagation

Status: **Proposed.** Captured 2026-08-07; ready for implementation. ## Implementation order and parallelism

Work is split into six phases. Within each phase all listed tasks are independent and can be
done in parallel. A phase must be fully complete before the next phase starts.

```
Phase 1 — serial (foundational infra, no code deps)
  S01/T01  Neo4j CORRELATES_WITH constraint, index, seed edges

Phase 2 — serial (foundational contract, everything imports this)
  S02/T01  ConditionCode UPSTREAM_UP/DOWN + PropagationHop model

Phase 3 — parallel (all depend only on Phase 2; none depend on each other)
  S02/T02  PredictionMade propagation fields
  S02/T03  CausalGraphClient: get_correlation_edges + update_correlation_weight
  S06/T01  CorrelationSample + CorrelationEdgeEstimate models

Phase 4 — parallel (all depend on Phase 3 complete; none depend on each other)
  S03/T01  Prediction pipeline: two-pass propagation loop + visited-set guard
  S03/T02  Prediction config: MAX_PROPAGATION_DEPTH field
  S04/T01  Credibility pipeline: CORRELATES_WITH Beta-Bernoulli update (online)
  S06/T02  Offline dataset: build_correlation_samples()
  S06/T03  Offline estimator: estimate_correlation_edges()
  S06/T04  Offline writer: write_correlation_estimates() + upsert_correlation_edge

Phase 5 — serial (needs all of Phase 4)
  S06/T05  Wire both learning paths into run_with() + correlation_learning_enabled config

Phase 6 — parallel (documentation; all need Phase 5 complete; independent of each other)
  S05/T01  ADR-008 decision record              (requirements/ADR-decisions.md)
  S05/T02  SyRS update                          (requirements/SyRS-system.md — §3 defs, §9.2 graph model, §4 flow, §8 messages)
  S05/T03  SRS-01 update                        (requirements/SRS-01-shared-foundation.md — ConditionCode, PredictionMade, PropagationHop, CorrelationEdge)
  S05/T04  SRS-04 update                        (requirements/SRS-04-prediction.md — propagation requirements)
  S05/T05  SRS-07 update                        (requirements/SRS-07-credibility.md — online + offline correlation paths)
  S05/T06  REF-01 update                        (requirements/REF-01-event-taxonomy.md — UPSTREAM_UP, UPSTREAM_DOWN condition codes)
  S05/T07  backlog/README.md E10 row verify     (backlog/README.md)
```

**Why each phase boundary is a hard stop:**
- Phase 1→2: S02/T01 imports `ConditionCode` which must map 1-to-1 with the Neo4j
  `condition` property on `CORRELATES_WITH` edges seeded in S01/T01.
- Phase 2→3: Every Phase 3 file imports `ConditionCode` or `PropagationHop` from S02/T01.
- Phase 3→4: S03/T01 calls `get_correlation_edges` (S02/T03) and reads
  `PredictionMade.propagation_chain` (S02/T02). S04/T01 reads `propagation_chain`.
  S06/T02 calls `get_correlation_edges`. S06/T03 and T04 import `CorrelationSample` /
  `CorrelationEdgeEstimate` from S06/T01.
- Phase 4→5: S06/T05 imports and calls `build_correlation_samples` (T02),
  `estimate_correlation_edges` (T03), and `write_correlation_estimates` (T04).
- Phase 5→6: Documentation must describe the fully implemented behaviour including
  both online (S04/T01) and offline (S06/T05) correlation learning.

## Motivation

The current prediction graph supports a single hop: `(:CausalFactor)-[:CAUSES]->(:Asset)`.
Every prediction is driven directly by a news event. The graph cannot model **capital-rotation**
or **correlation chains** where a price move in one asset is itself the cause of a directional
move in another.

Concrete example discussed in design:

```
"Iran attacks Arab countries"
        |
        v  MILITARY_CONFLICT / TRANSPORT_AFFECTED
  XOM_NYSE UP  (oil proxy — existing, works today)
        |
        v  capital rotation: oil up → money leaves gold
  NEM_NYSE DOWN  (gold proxy — NOT predicted today, no Asset→Asset edge)
```

The system predicts `XOM_NYSE UP` correctly but has no mechanism to propagate that result to
`NEM_NYSE DOWN`. This epic closes that gap.

A second design point confirmed during discussion: simple one-hop events such as
"US government raises interest rate → gold down" do **not** need this feature. `RATE_DECISION`
already has a direct `CAUSES` edge to `NEM_NYSE` with `direction = DOWN`. Cross-asset
propagation is only needed when the causal driver is another asset's predicted price move, not
a news event.

## Architecture changes — overview

Six layers of the system change across six stories:

| Story | Layer | Summary |
|---|---|---|
| S01 | Neo4j / infra | New `CORRELATES_WITH` edge type, seed data, index |
| S02 | Shared library (`src/shared/`) | Two new `ConditionCode` values, `PropagationHop` model, two new graph-client methods, two new fields on `PredictionMade` |
| S03 | Prediction Service | Two-pass propagation loop in `pipeline.py`, visited-set cycle guard, depth cap |
| S04 | Credibility Service | Beta-Bernoulli update for `CORRELATES_WITH` edges from `propagation_chain` |
| S05 | Documentation | ADR-008; SyRS, SRS-01, SRS-04, SRS-07, REF-01 updates; backlog README verify (7 tasks, all parallel) |
| S06 | Offline correlation learner | Second data path inside the existing learner: discovers and refines `CORRELATES_WITH` edges from historical scored predictions |

## Non-negotiable constraints

- **Zero LLM calls added.** M1 graph-only policy is unchanged.
- **`decide()` is not modified.** It stays single-asset and stateless; propagation is a
  pipeline-loop concern only.
- **Depth cap: 3 hops maximum.** Configurable via `MAX_PROPAGATION_DEPTH` env var
  (default 3).
- **Visited-set cycle guard.** Once an asset is decided in any pass, it is never decided
  again in the same pipeline run regardless of remaining graph edges.
- **Force summation, not winner-takes-all.** Conflicting propagated forces feed into
  `decide()` and cancel naturally; a net ratio below the deadband produces no prediction.
- **Each propagated prediction is independently scoreable.** It produces its own
  `PredictionMade`, `PriceRequested`, `PriceObserved`, `PredictionScored` lifecycle.
  Credit flows back only to the edge that directly produced that prediction.

## Overall acceptance criteria

1. `MILITARY_CONFLICT / TRANSPORT_AFFECTED` event with `XOM_NYSE` in scope → `XOM_NYSE`
   predicted (existing) **and** `NEM_NYSE DOWN` propagated in the same pipeline run when a
   seeded `CORRELATES_WITH(XOM_NYSE → NEM_NYSE, condition=UPSTREAM_UP, direction=DOWN)` edge
   exists.
2. A graph cycle `A → B → A` produces one prediction for `A` and one for `B`; the revisit of
   `A` is silenced by the visited-set guard.
3. Two upstream assets sending opposite propagated forces to the same downstream asset resolve
   via force summation; if they cancel below the deadband no downstream prediction is emitted.
4. Every propagated `PredictionMade` carries `propagation_depth ≥ 1` and a non-empty
   `propagation_chain`.
5. Credibility updates the `CORRELATES_WITH` edge when a propagated prediction is scored, not
   the originating `CausalFactor` edge.
6. All pre-existing unit and integration tests continue to pass.
7. `mypy --strict` and `ruff check` pass on every changed file.
8. ADR-008, SRS-04, and SRS-07 are updated in the same commit as the code.
