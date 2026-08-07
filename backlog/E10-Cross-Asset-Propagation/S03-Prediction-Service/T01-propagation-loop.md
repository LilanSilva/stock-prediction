# T01 — Two-Pass Propagation Loop in `pipeline.py`

## Context

`src/services/prediction/prediction/pipeline.py` currently calls `decide()` once per asset
for each incoming `EventDetected`. This task adds a propagation loop that runs additional
passes: for every asset that received a directional prediction in pass N, it queries
`CORRELATES_WITH` edges and calls `decide()` for their downstream targets in pass N+1.

## How the loop works

```
Pass 0 (direct):
  For each asset in EventDetected.asset_ids:
    edges = graph_client.get_firing_edges(event_type, [asset], conditions)
    decision = decide(asset, edges, ...)
    if decision is directional:
      emit PredictionMade(propagation_depth=0, propagation_chain=[])
      new_predictions[asset] = decision.direction

Pass 1 (propagation):
  For each (source_asset, direction) in new_predictions from pass 0:
    condition = UPSTREAM_UP if direction == UP else UPSTREAM_DOWN
    corr_edges = graph_client.get_correlation_edges(source_asset, condition)
    for each corr_edge in corr_edges:
      target = corr_edge.target_asset_id
      if target in visited: continue          # cycle guard
      visited.add(target)
      # Build a synthetic FiringEdge from the CorrelationEdge for decide()
      firing = FiringEdge(
          factor_id=...,         # use a sentinel or pass corr_edge directly — see note below
          asset_id=target,
          direction=corr_edge.direction,
          weight=corr_edge.weight,
          confidence=corr_edge.confidence,
          alpha=corr_edge.alpha,
          beta=corr_edge.beta,
      )
      decision = decide(target, [firing], ...)
      if decision is directional:
        emit PredictionMade(
            propagation_depth=current_depth,
            propagation_chain=[PropagationHop(
                source_asset_id=source_asset,
                target_asset_id=target,
                condition=condition,
                direction=decision.direction,
                edge_weight=corr_edge.weight,
            )]
        )
        next_pass_predictions[target] = decision.direction

Repeat until next_pass_predictions is empty OR current_depth >= MAX_PROPAGATION_DEPTH.
```

### Note on `FiringEdge.factor_id`

`FiringEdge` requires a `factor_id: EventType`. For propagated edges there is no causal
factor — the source is an asset. Two acceptable approaches:

- **Option A (simpler):** add `factor_id: EventType | None = None` to `FiringEdge` and let
  `decide()` skip it (it only uses `direction`, `weight`, `alpha`, `beta`). Check whether
  `decide()` actually reads `factor_id` — if it only reads it in `_rationale()`, a `None`
  guard there is sufficient.
- **Option B (no model change):** introduce a separate `CorrelationFiringEdge` type accepted
  by `decide()` via a protocol/union. More type-safe but more surface area.

Recommendation: **Option A**. Read `decision.py` and confirm `factor_id` is only used in
`_rationale()`. If so, a `None` guard in that one function is the minimum change.

## Files to change

### `src/services/prediction/prediction/pipeline.py`

The exact lines to change depend on the current pipeline shape. Read the file before editing.
The general insertion points are:

1. After the existing per-asset `decide()` call, collect results into a
   `dict[AssetId, Direction]` named `direct_decisions`.
2. Add the propagation loop below, using `MAX_PROPAGATION_DEPTH` from config.
3. Accumulate all `PredictionMade` objects (direct + propagated) and persist + publish them
   in the existing outbox sweep at the end — do not add a separate publish path.

### `src/services/prediction/prediction/decision.py` (minimal, if Option A)

In `_rationale()`, guard the `factor_id` reference:

```python
factor_str = edge.factor_id.value if edge.factor_id is not None else "CORRELATION"
parts = [f"{factor_str}{_ARROW[eff]}{e.weight:.2f}" for e, eff in edges]
```

### `src/shared/shared/graph/models.py` (if Option A)

Make `factor_id` optional on `FiringEdge`:

```python
factor_id: EventType | None = None
```

Check whether any existing code accesses `edge.factor_id` and raises on `None` before
making this change. `edge_id` uses `factor_id` — add a guard:

```python
@property
def edge_id(self) -> str:
    target = self.inherited_from or self.asset_id.value
    prefix = self.factor_id.value if self.factor_id is not None else "CORRELATION"
    if self.condition is None:
        return f"{prefix}->{target}"
    return f"{prefix}|{self.condition.value}->{target}"
```

## Unit tests to add

**File:** `src/services/prediction/tests/test_pipeline.py`

Read the existing tests first. Add alongside them, do not duplicate setup.

```python
async def test_propagation_produces_downstream_prediction(
    pipeline, mock_graph_client, mock_correlation_edges
):
    # Arrange: direct edge fires for XOM_NYSE UP; correlation edge XOM_NYSE→NEM_NYSE DOWN exists.
    mock_graph_client.get_firing_edges.return_value = [
        make_firing_edge(asset_id=AssetId.XOM_NYSE, direction=Direction.UP, weight=0.65)
    ]
    mock_graph_client.get_correlation_edges.return_value = [
        make_correlation_edge(
            source=AssetId.XOM_NYSE, target=AssetId.NEM_NYSE,
            condition=ConditionCode.UPSTREAM_UP, direction=Direction.DOWN, weight=0.45,
        )
    ]

    predictions = await pipeline.run(make_event_detected(asset_ids=[AssetId.XOM_NYSE]))

    assert len(predictions) == 2
    direct = next(p for p in predictions if p.asset_id == AssetId.XOM_NYSE)
    propagated = next(p for p in predictions if p.asset_id == AssetId.NEM_NYSE)
    assert direct.propagation_depth == 0
    assert propagated.propagation_depth == 1
    assert propagated.propagation_chain[0].source_asset_id == AssetId.XOM_NYSE


async def test_visited_set_prevents_cycle(pipeline, mock_graph_client):
    # A→B→A: B's outbound edge points back to A, which is already in visited.
    mock_graph_client.get_firing_edges.return_value = [
        make_firing_edge(asset_id=AssetId.XOM_NYSE, direction=Direction.UP, weight=0.65)
    ]
    mock_graph_client.get_correlation_edges.side_effect = [
        # Pass 1: XOM_NYSE → NEM_NYSE
        [make_correlation_edge(AssetId.XOM_NYSE, AssetId.NEM_NYSE,
                               ConditionCode.UPSTREAM_UP, Direction.DOWN, 0.45)],
        # Pass 2: NEM_NYSE → XOM_NYSE (cycle) — should be skipped by visited set
        [make_correlation_edge(AssetId.NEM_NYSE, AssetId.XOM_NYSE,
                               ConditionCode.UPSTREAM_DOWN, Direction.UP, 0.30)],
    ]
    predictions = await pipeline.run(make_event_detected(asset_ids=[AssetId.XOM_NYSE]))

    asset_ids = [p.asset_id for p in predictions]
    assert asset_ids.count(AssetId.XOM_NYSE) == 1  # visited guard fired, no duplicate


async def test_depth_cap_stops_propagation(pipeline, mock_graph_client):
    # Chain A→B→C→D; cap at depth 2 → D never predicted.
    # Set MAX_PROPAGATION_DEPTH=2 via config override.
    ...  # construct a 3-hop chain, assert only 3 predictions (A, B, C), not 4


async def test_conflicting_propagation_cancels_to_neutral(pipeline, mock_graph_client):
    # Two upstream assets send opposing forces to the same target; net ratio < deadband.
    # No prediction emitted for the target.
    ...
```

Implement the last two tests fully, following the same mock pattern as the first two.

## Acceptance criteria

1. A direct prediction for `XOM_NYSE UP` triggers a propagated prediction for `NEM_NYSE DOWN`
   when a `CORRELATES_WITH(XOM_NYSE, UPSTREAM_UP, NEM_NYSE, DOWN)` edge exists.
2. A graph cycle `A → B → A` produces exactly one prediction per asset; the revisit is
   silently skipped.
3. Propagation stops when `propagation_depth == MAX_PROPAGATION_DEPTH` even if further
   `CORRELATES_WITH` edges exist.
4. Two opposing propagated forces on the same target cancel below the deadband; no prediction
   is emitted for that target.
5. Propagated `PredictionMade` has `propagation_depth >= 1` and a non-empty
   `propagation_chain`.
6. Direct `PredictionMade` has `propagation_depth == 0` and an empty `propagation_chain` —
   existing behaviour unchanged.
7. All four new unit tests pass.
8. All pre-existing `test_pipeline.py` and `test_decision.py` tests still pass.
9. `mypy --strict` and `ruff check` clean on all changed files.

## Definition of done

- [ ] Propagation loop implemented in `pipeline.py`
- [ ] `visited` set guard implemented (same pipeline run, per-asset unique)
- [ ] `MAX_PROPAGATION_DEPTH` config key read from config (see T02)
- [ ] Option A or B chosen and documented in a short inline comment
- [ ] `factor_id` optional guard applied if Option A
- [ ] Four unit tests added and passing
- [ ] No existing test broken
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
