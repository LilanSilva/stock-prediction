# T01 — `CorrelationSample` and `CorrelationEdgeEstimate` Models

## Context

The existing `Sample` and `EdgeEstimate` dataclasses in
`src/services/credibility/credibility/learning/models.py` use `factor: EventType` as the
upstream source. For `CORRELATES_WITH` edges the upstream source is an `AssetId`, not an
`EventType`. Merging the two into one model would force optional fields throughout the
estimator. Instead, add two new dataclasses to the same file alongside the existing ones.

## File to change

**`src/services/credibility/credibility/learning/models.py`**

Add after the existing `EdgeEstimate` dataclass:

```python
@dataclass(frozen=True, slots=True)
class CorrelationSample:
    """One realised observation for a CORRELATES_WITH edge.

    Records whether the target asset moved in the direction the correlation edge predicts,
    given that the source asset was predicted directional (UPSTREAM_UP or UPSTREAM_DOWN) and
    that prediction was scored in the same settlement session.

    ``source_asset``  — the asset that was predicted (propagation_depth=0).
    ``condition``     — UPSTREAM_UP when the source was predicted UP, UPSTREAM_DOWN when DOWN.
    ``target_asset``  — the asset whose actual return is being observed.
    ``actual_return`` — (settlement_close - baseline_close) / baseline_close for target_asset
                        in the same settlement session as the source prediction.
    ``is_abnormal``   — True when |actual_return| >= abnormal_threshold * target volatility.
    ``asset_volatility`` — std dev of target_asset daily returns over the volatility window;
                           0.0 when fewer than two closes available.
    """

    source_asset: AssetId
    condition: ConditionCode          # always UPSTREAM_UP or UPSTREAM_DOWN
    target_asset: AssetId
    actual_return: float
    is_abnormal: bool = False
    asset_volatility: float = 0.0


@dataclass(frozen=True, slots=True)
class CorrelationEdgeEstimate:
    """A data-derived CORRELATES_WITH edge proposal for one (source_asset, condition, target_asset) group."""

    source_asset: AssetId
    condition: ConditionCode
    target_asset: AssetId
    direction: Direction
    weight: float
    confidence: float
    alpha: float
    beta: float
    sample_count: int
```

`ConditionCode` is already imported in this file via `shared.schemas.messages`. Verify the
import list covers `ConditionCode` before saving — add it if missing.

## Unit tests to add

**File:** `src/services/credibility/tests/test_learning_models.py` if it exists, otherwise
add to the nearest models or estimator test file. Do not create a duplicate file.

```python
def test_correlation_sample_is_frozen():
    s = CorrelationSample(
        source_asset=AssetId.XOM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        target_asset=AssetId.NEM_NYSE,
        actual_return=-0.02,
    )
    with pytest.raises(Exception):
        s.source_asset = AssetId.NEM_NYSE  # type: ignore[misc]


def test_correlation_sample_defaults():
    s = CorrelationSample(
        source_asset=AssetId.XOM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        target_asset=AssetId.NEM_NYSE,
        actual_return=0.01,
    )
    assert s.is_abnormal is False
    assert s.asset_volatility == 0.0


def test_correlation_edge_estimate_fields():
    e = CorrelationEdgeEstimate(
        source_asset=AssetId.XOM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        target_asset=AssetId.NEM_NYSE,
        direction=Direction.DOWN,
        weight=0.4,
        confidence=0.7,
        alpha=4.0,
        beta=2.0,
        sample_count=5,
    )
    assert e.source_asset == AssetId.XOM_NYSE
    assert e.direction == Direction.DOWN
    assert e.sample_count == 5
```

## Acceptance criteria

1. `CorrelationSample` and `CorrelationEdgeEstimate` are importable from
   `credibility.learning.models`.
2. Both are frozen dataclasses — mutation raises.
3. `CorrelationSample` defaults: `is_abnormal=False`, `asset_volatility=0.0`.
4. All three unit tests pass.
5. All pre-existing model tests still pass.
6. `mypy --strict` and `ruff check` clean.

## Definition of done

- [ ] `CorrelationSample` added to `models.py`
- [ ] `CorrelationEdgeEstimate` added to `models.py`
- [ ] `ConditionCode` import verified / added
- [ ] Three unit tests added and passing
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
