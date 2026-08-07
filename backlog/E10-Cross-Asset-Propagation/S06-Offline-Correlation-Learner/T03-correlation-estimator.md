# T03 — `estimate_correlation_edges()` in `estimator.py`

## Context

The existing `estimate_edges()` groups `Sample` objects by `(EventType, ConditionCode, AssetId)`
and produces `EdgeEstimate` objects. The correlation estimator does the same math on
`CorrelationSample` objects grouped by `(AssetId_source, ConditionCode, AssetId_target)` and
produces `CorrelationEdgeEstimate` objects.

The Beta-Bernoulli estimation math is identical — only the grouping key shape differs.

## File to change

**`src/services/credibility/credibility/learning/estimator.py`**

Read the full file before editing. Add the new function after the existing `estimate_edges`.
Do not modify `estimate_edges`.

```python
_CorrGroupKey = tuple[AssetId, ConditionCode, AssetId]


def estimate_correlation_edges(
    samples: list[CorrelationSample],
    *,
    deadband: float,
    min_samples: int,
) -> list[CorrelationEdgeEstimate]:
    """Aggregate correlation samples into CORRELATES_WITH edge estimates.

    Groups by (source_asset, condition, target_asset). Groups with fewer than min_samples
    observations are dropped unless any sample is flagged is_abnormal=True, in which case
    a single observation is sufficient (same rule as estimate_edges).

    Direction is determined by the mean actual_return of the target asset:
    - positive mean > deadband  → UP  (target tends to rise when source is predicted UP/DOWN)
    - negative mean < -deadband → DOWN
    - |mean| <= deadband        → NEUTRAL (dropped — no directional signal)

    Note: unlike CAUSES edges, actual_return here is NOT sign-flipped for polarity. There is
    no polarity concept for CORRELATES_WITH edges — the condition (UPSTREAM_UP/UPSTREAM_DOWN)
    already encodes the upstream direction, and the target return is measured as-is.
    """
    grouped: dict[_CorrGroupKey, list[CorrelationSample]] = defaultdict(list)
    for sample in samples:
        grouped[(sample.source_asset, sample.condition, sample.target_asset)].append(sample)

    estimates: list[CorrelationEdgeEstimate] = []
    for (source, condition, target), group_samples in grouped.items():
        total = len(group_samples)
        effective_min = 1 if any(s.is_abnormal for s in group_samples) else min_samples
        if total < effective_min:
            continue

        returns = [s.actual_return for s in group_samples]
        mean = sum(returns) / total
        positives = sum(1 for r in returns if r > 0.0)
        negatives = sum(1 for r in returns if r < 0.0)

        if mean > deadband:
            direction = Direction.UP
            agreeing = positives
        elif mean < -deadband:
            direction = Direction.DOWN
            agreeing = negatives
        else:
            direction = Direction.NEUTRAL
            agreeing = 0

        disagreeing = total - agreeing
        estimates.append(
            CorrelationEdgeEstimate(
                source_asset=source,
                condition=condition,
                target_asset=target,
                direction=direction,
                weight=min(1.0, abs(mean) / _WEIGHT_RETURN_SCALE),
                confidence=agreeing / total,
                alpha=agreeing + _PRIOR,
                beta=disagreeing + _PRIOR,
                sample_count=total,
            )
        )

    estimates.sort(
        key=lambda e: (e.source_asset.value, e.condition.value, e.target_asset.value)
    )
    return estimates
```

`_WEIGHT_RETURN_SCALE` and `_PRIOR` are already defined in the file — reuse them. Do not
redefine.

Also add the required imports at the top of the file:

```python
from credibility.learning.models import CorrelationEdgeEstimate, CorrelationSample
```

Verify the existing import line covers `EdgeEstimate` and `Sample` — extend it rather than
adding a duplicate import statement.

## Unit tests to add

**File:** `src/services/credibility/tests/test_learning_estimator.py` — add to existing file.

```python
def test_estimate_correlation_edges_empty_input():
    result = estimate_correlation_edges([], deadband=0.002, min_samples=5)
    assert result == []


def test_estimate_correlation_edges_below_min_samples_dropped():
    samples = [
        CorrelationSample(
            source_asset=AssetId.XOM_NYSE,
            condition=ConditionCode.UPSTREAM_UP,
            target_asset=AssetId.NEM_NYSE,
            actual_return=-0.02,
        )
    ]
    # 1 sample, min_samples=5 → dropped (not abnormal)
    result = estimate_correlation_edges(samples, deadband=0.002, min_samples=5)
    assert result == []


def test_estimate_correlation_edges_abnormal_bypasses_min_samples():
    samples = [
        CorrelationSample(
            source_asset=AssetId.XOM_NYSE,
            condition=ConditionCode.UPSTREAM_UP,
            target_asset=AssetId.NEM_NYSE,
            actual_return=-0.05,
            is_abnormal=True,
        )
    ]
    result = estimate_correlation_edges(samples, deadband=0.002, min_samples=5)
    assert len(result) == 1
    assert result[0].direction == Direction.DOWN


def test_estimate_correlation_edges_direction_up():
    samples = [
        CorrelationSample(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP,
                          AssetId.NEM_NYSE, actual_return=0.03)
        for _ in range(5)
    ]
    result = estimate_correlation_edges(samples, deadband=0.002, min_samples=5)
    assert len(result) == 1
    assert result[0].direction == Direction.UP
    assert result[0].source_asset == AssetId.XOM_NYSE
    assert result[0].target_asset == AssetId.NEM_NYSE


def test_estimate_correlation_edges_neutral_dropped():
    # Mean return within deadband → NEUTRAL → dropped
    samples = [
        CorrelationSample(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP,
                          AssetId.NEM_NYSE, actual_return=0.001)
        for _ in range(5)
    ]
    result = estimate_correlation_edges(samples, deadband=0.002, min_samples=5)
    assert result == []


def test_estimate_correlation_edges_alpha_beta_counts():
    # 4 samples DOWN, 1 UP → agreeing=4 negatives, disagreeing=1
    samples = (
        [CorrelationSample(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP,
                           AssetId.NEM_NYSE, actual_return=-0.03)] * 4
        + [CorrelationSample(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP,
                             AssetId.NEM_NYSE, actual_return=0.01)]
    )
    result = estimate_correlation_edges(samples, deadband=0.002, min_samples=5)
    assert len(result) == 1
    assert result[0].alpha == pytest.approx(4 + 1.0)   # agreeing + PRIOR
    assert result[0].beta  == pytest.approx(1 + 1.0)   # disagreeing + PRIOR


def test_estimate_correlation_edges_sorted_output():
    samples_a = [
        CorrelationSample(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP,
                          AssetId.NEM_NYSE, actual_return=-0.03)
        for _ in range(5)
    ]
    samples_b = [
        CorrelationSample(AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP,
                          AssetId.XOM_NYSE, actual_return=-0.02)
        for _ in range(5)
    ]
    result = estimate_correlation_edges(samples_a + samples_b, deadband=0.002, min_samples=5)
    keys = [(e.source_asset.value, e.target_asset.value) for e in result]
    assert keys == sorted(keys)
```

## Acceptance criteria

1. Empty input → empty output, no exception.
2. Groups below `min_samples` are dropped unless `is_abnormal=True`.
3. An abnormal sample bypasses `min_samples` — one sample is enough.
4. Mean return above deadband → `UP`; below → `DOWN`; within → `NEUTRAL` (dropped).
5. `alpha = agreeing + 1.0`, `beta = disagreeing + 1.0` (Beta(1,1) prior).
6. Output is sorted by `(source_asset, condition, target_asset)`.
7. All seven unit tests pass.
8. All pre-existing `test_learning_estimator.py` tests still pass.
9. `mypy --strict` and `ruff check` clean.

## Definition of done

- [ ] `estimate_correlation_edges()` added to `estimator.py`
- [ ] Imports extended to cover `CorrelationSample`, `CorrelationEdgeEstimate`
- [ ] Seven unit tests added and passing
- [ ] No existing test broken
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
