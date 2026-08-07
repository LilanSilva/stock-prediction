# S04 — Credibility Service: Learn from Propagated Predictions

## Overview

The Credibility Service currently updates only `CAUSES` edges (`CausalFactor → Asset`) when a
`PredictionScored` message arrives. After S03, some `PredictionMade` messages will have a
non-empty `propagation_chain`. This story teaches Credibility to detect those messages and
update the correct `CORRELATES_WITH` edges instead.

The existing `CAUSES`-edge update path is unchanged. The new path is additive.

## Dependencies

- S02 T02 (`PredictionMade.propagation_chain` field) must be merged.
- S02 T03 (`update_correlation_weight` on `CausalGraphClient`) must be merged.
- S03 must be merged so propagated predictions are actually produced.

## Tasks

| Task | Summary |
|---|---|
| [T01](T01-credibility-propagation-update.md) | Detect propagation chain and update `CORRELATES_WITH` edge |

## How to test end-to-end

1. Trigger a pipeline run that produces a propagated prediction (see S03 e2e steps).
2. Wait for Verification to score the propagated prediction.
3. Inspect Neo4j:

```cypher
MATCH (a1:Asset {id: 'XOM_NYSE'})-[r:CORRELATES_WITH {condition: 'UPSTREAM_UP'}]->
      (a2:Asset {id: 'NEM_NYSE'})
RETURN r.alpha, r.beta
```

Expected: `alpha` or `beta` has incremented from the seed value of `1.0` depending on whether
the prediction was correct.
