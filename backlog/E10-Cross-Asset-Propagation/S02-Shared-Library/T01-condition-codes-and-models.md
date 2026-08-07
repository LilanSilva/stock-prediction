# T01 — New `ConditionCode` Values and `PropagationHop` Model

## Context

`ConditionCode` in `src/shared/shared/schemas/messages.py` currently has three values:
`TRANSPORT_AFFECTED`, `SAFE_HAVEN_ONLY`, and `RISK_PREMIUM_ELEVATED`. The propagation query
(S03) needs two new values to gate `CORRELATES_WITH` edges: `UPSTREAM_UP` (the source asset
was predicted UP in this pipeline run) and `UPSTREAM_DOWN` (predicted DOWN).

A `PropagationHop` model is also needed to record each `CORRELATES_WITH` edge that fired, so
Credibility can update the right edge when a propagated prediction is scored.

## Files to change

### 1. `src/shared/shared/schemas/messages.py`

**Add to `ConditionCode` enum** (find the existing enum and append):

```python
UPSTREAM_UP   = "UPSTREAM_UP"    # source asset predicted UP in this pipeline run
UPSTREAM_DOWN = "UPSTREAM_DOWN"  # source asset predicted DOWN in this pipeline run
```

**Add new model** (place near `ContributingEdge`):

```python
class PropagationHop(BaseModel):
    """One fired (:Asset)-[:CORRELATES_WITH]->(:Asset) edge in a propagation chain.

    Records the source asset, target asset, condition that gated the edge, and the direction
    the edge contributed to the target's decision — all needed by Credibility to update the
    correct CORRELATES_WITH edge when the prediction is scored.
    """
    model_config = ConfigDict(frozen=True)

    source_asset_id: AssetId
    target_asset_id: AssetId
    condition: ConditionCode          # UPSTREAM_UP or UPSTREAM_DOWN
    direction: Direction              # direction contributed to the target (after force summation)
    edge_weight: float                # expert weight of the fired edge
```

## Unit tests to add

**File:** `src/shared/tests/test_schemas.py` (or the existing schema test file — check what
already exists before creating a new file)

```python
def test_condition_code_upstream_values_exist():
    assert ConditionCode.UPSTREAM_UP   == "UPSTREAM_UP"
    assert ConditionCode.UPSTREAM_DOWN == "UPSTREAM_DOWN"


def test_propagation_hop_is_frozen():
    hop = PropagationHop(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        direction=Direction.DOWN,
        edge_weight=0.45,
    )
    with pytest.raises(Exception):
        hop.source_asset_id = AssetId.NEM_NYSE  # type: ignore[misc]


def test_propagation_hop_round_trips_json():
    hop = PropagationHop(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        direction=Direction.DOWN,
        edge_weight=0.45,
    )
    assert PropagationHop.model_validate_json(hop.model_dump_json()) == hop
```

## Acceptance criteria

1. `ConditionCode.UPSTREAM_UP` and `ConditionCode.UPSTREAM_DOWN` are importable from
   `shared.schemas.messages`.
2. `PropagationHop` is importable from `shared.schemas.messages`.
3. `PropagationHop` is frozen (mutating any field raises `ValidationError`).
4. All three new unit tests pass.
5. `mypy --strict` reports zero errors on `shared/schemas/messages.py`.
6. All pre-existing shared schema tests still pass.

## Definition of done

- [ ] Two new `ConditionCode` values added
- [ ] `PropagationHop` model added with correct field types
- [ ] Three unit tests added and passing
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
