# T01 — Update `CORRELATES_WITH` Edge from `propagation_chain`

## Context

`src/services/credibility/credibility/pipeline.py` (or equivalent handler) processes
`PredictionScored` messages and updates `alpha`/`beta` on the `CAUSES` edge that produced
the prediction. After S03, some scored predictions carry a non-empty `propagation_chain`.

When `propagation_chain` is non-empty the prediction was produced by a `CORRELATES_WITH`
edge, not a `CAUSES` edge. Credibility must update the correlation edge, not invent a phantom
`CAUSES` update.

The scoring rule is: **each hop in the chain is updated only if it is the direct cause of
this specific prediction**. A hop at index `i` is the direct cause of a prediction at
`propagation_depth == i`. Because each propagated `PredictionMade` is independently
scored, the correct hop is always the **last element** in `propagation_chain` (the one whose
target is the scored asset).

## Decision logic

Before reading the file, confirm the existing update path by reading
`src/services/credibility/credibility/pipeline.py`. Then apply this logic:

```python
if scored_prediction.propagation_chain:
    # Propagated prediction: update the CORRELATES_WITH edge that directly produced it.
    last_hop = scored_prediction.propagation_chain[-1]
    alpha, beta = current_alpha_beta(last_hop)  # read from Neo4j or derive from PredictionMade
    if scored_prediction.is_correct:
        alpha += 1.0
    else:
        beta += 1.0
    await graph_client.update_correlation_weight(
        source_asset_id=last_hop.source_asset_id,
        target_asset_id=last_hop.target_asset_id,
        condition=last_hop.condition,
        alpha=alpha,
        beta=beta,
    )
else:
    # Direct prediction: existing CAUSES-edge update path, unchanged.
    ...existing code...
```

### How to get the current `alpha`/`beta` for a `CORRELATES_WITH` edge

`CausalGraphClient` has no `get_correlation_edge_counts` method yet. Two options:

- **Option A (simpler):** read the current counts from the `CorrelationEdge` returned by
  `get_correlation_edges()` — but that requires a second graph query. Only viable if Credibility
  already queries the graph per message.
- **Option B (consistent with `CAUSES` path):** add a
  `get_correlation_edge_counts(source, target, condition) -> tuple[float, float] | None`
  method to `CausalGraphClient` (model it on the existing `get_group_edge_counts`). Add this
  method in `src/shared/shared/graph/client.py` using the same Cypher pattern:

```python
_CORRELATION_EDGE_COUNTS_CYPHER = """
MATCH (a1:Asset {id: $source_asset_id})-[r:CORRELATES_WITH {condition: $condition}]->
      (a2:Asset {id: $target_asset_id})
RETURN r.alpha AS alpha, r.beta AS beta
"""
```

Check how `CAUSES`-edge Credibility currently reads `alpha`/`beta` and follow the same
pattern for `CORRELATES_WITH` edges. The two paths should mirror each other.

## Files to change

1. `src/services/credibility/credibility/pipeline.py` — add the propagation branch.
2. `src/shared/shared/graph/client.py` — add `get_correlation_edge_counts` if Option B.
3. `src/shared/shared/graph/__init__.py` — export any new public method.

## Unit tests to add

**File:** `src/services/credibility/tests/test_pipeline.py`

Read the existing tests first. Add these alongside them:

```python
async def test_correct_propagated_prediction_increments_alpha(
    credibility_pipeline, mock_graph_client
):
    hop = PropagationHop(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        direction=Direction.DOWN,
        edge_weight=0.45,
    )
    scored = make_prediction_scored(
        asset_id=AssetId.NEM_NYSE,
        is_correct=True,
        propagation_chain=[hop],
    )
    mock_graph_client.get_correlation_edge_counts.return_value = (1.0, 1.0)

    await credibility_pipeline.handle(scored)

    mock_graph_client.update_correlation_weight.assert_called_once_with(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        alpha=2.0,
        beta=1.0,
    )


async def test_wrong_propagated_prediction_increments_beta(
    credibility_pipeline, mock_graph_client
):
    hop = PropagationHop(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        direction=Direction.DOWN,
        edge_weight=0.45,
    )
    scored = make_prediction_scored(
        asset_id=AssetId.NEM_NYSE,
        is_correct=False,
        propagation_chain=[hop],
    )
    mock_graph_client.get_correlation_edge_counts.return_value = (1.0, 1.0)

    await credibility_pipeline.handle(scored)

    mock_graph_client.update_correlation_weight.assert_called_once_with(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        alpha=1.0,
        beta=2.0,
    )


async def test_direct_prediction_does_not_call_update_correlation_weight(
    credibility_pipeline, mock_graph_client
):
    # Ensure the existing path is unchanged: a direct prediction (empty chain)
    # must NOT call update_correlation_weight.
    scored = make_prediction_scored(
        asset_id=AssetId.NEM_NYSE,
        is_correct=True,
        propagation_chain=[],    # direct prediction
    )
    await credibility_pipeline.handle(scored)

    mock_graph_client.update_correlation_weight.assert_not_called()
```

## Acceptance criteria

1. A scored `PredictionMade` with a non-empty `propagation_chain` causes Credibility to call
   `update_correlation_weight` on the last hop's `(source, target, condition)`.
2. A correct propagated prediction increments `alpha` by 1.0; an incorrect one increments
   `beta` by 1.0.
3. A direct prediction (empty `propagation_chain`) follows the existing `CAUSES`-edge update
   path — `update_correlation_weight` is never called.
4. All three new unit tests pass.
5. All pre-existing Credibility tests still pass.
6. `mypy --strict` and `ruff check` clean on all changed files.

## Definition of done

- [ ] Propagation branch added to Credibility pipeline handler
- [ ] `get_correlation_edge_counts` added to `CausalGraphClient` (if Option B chosen)
- [ ] Three unit tests added and passing
- [ ] No existing test broken
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
