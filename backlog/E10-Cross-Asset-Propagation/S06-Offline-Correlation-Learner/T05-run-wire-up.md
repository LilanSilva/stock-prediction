# T05 — Wire Both Paths into `run_with()` and Extend `LearningSettings`

## Context

`run.py` currently calls `build_samples → estimate_edges → write_estimates` once and returns
the edge count. This task extends `run_with()` to call the correlation path sequentially after
the existing path. It also adds one new config field to `LearningSettings` so the correlation
path can be independently disabled during rollout without touching the CAUSES path.

The APScheduler job in `app.py` calls `run_with()` — it does not need to change. Scheduling,
`max_instances=1`, and `coalesce=True` are already in place.

## Part 1 — Extend `LearningSettings` in `config.py`

**File:** `src/services/credibility/credibility/learning/config.py`

Add one field inside `LearningSettings`:

```python
# Set to False to run only the CAUSES-edge path during rollout/debugging.
correlation_learning_enabled: bool = Field(default=True)
```

Env var: `CREDIBILITY_LEARNING_CORRELATION_LEARNING_ENABLED` (pydantic-settings uppercases
the field name and prepends the prefix). The default `True` means both paths run by default
once the code is deployed.

## Part 2 — Extend `run_with()` in `run.py`

**File:** `src/services/credibility/credibility/learning/run.py`

Read the full file before editing. The current `run_with` signature is:

```python
async def run_with(
    pool: asyncpg.Pool, graph: CausalGraphClient, settings: LearningSettings
) -> int:
```

Replace the body with:

```python
async def run_with(
    pool: asyncpg.Pool, graph: CausalGraphClient, settings: LearningSettings
) -> int:
    """Run one learning pass covering both CAUSES and CORRELATES_WITH edges; return total edges written."""

    # --- Path 1: CAUSES edges (unchanged) ---
    samples = await build_samples(
        pool,
        lookback_days=settings.lookback_days,
        volatility_lookback_days=settings.volatility_lookback_days,
        abnormal_threshold=settings.abnormal_threshold,
    )
    estimates = estimate_edges(
        samples, deadband=settings.deadband, min_samples=settings.min_samples
    )
    written_causes = await write_estimates(graph, estimates)

    # --- Path 2: CORRELATES_WITH edges ---
    written_corr = 0
    if settings.correlation_learning_enabled:
        corr_samples = await build_correlation_samples(
            pool,
            graph,
            lookback_days=settings.lookback_days,
            volatility_lookback_days=settings.volatility_lookback_days,
            abnormal_threshold=settings.abnormal_threshold,
        )
        corr_estimates = estimate_correlation_edges(
            corr_samples, deadband=settings.deadband, min_samples=settings.min_samples
        )
        written_corr = await write_correlation_estimates(graph, corr_estimates)

    total = written_causes + written_corr
    logger.info(
        "learning_run_complete",
        samples=len(samples),
        estimates=len(estimates),
        written_causes=written_causes,
        written_corr=written_corr,
        edges_written=total,
        lookback_days=settings.lookback_days,
        deadband=settings.deadband,
        min_samples=settings.min_samples,
        correlation_enabled=settings.correlation_learning_enabled,
    )
    return total
```

Add the new imports at the top of `run.py`:

```python
from credibility.learning.dataset import build_correlation_samples, build_samples
from credibility.learning.estimator import estimate_correlation_edges, estimate_edges
from credibility.learning.seed_writer import write_correlation_estimates, write_estimates
```

If the file already imports `build_samples`, `estimate_edges`, `write_estimates` individually,
extend those import lines rather than adding duplicates.

## Unit tests to add

**File:** `src/services/credibility/tests/` — check if a `test_learning_run.py` exists. If
not, create it. It must not require live infrastructure (mock pool and graph).

```python
async def test_run_with_calls_both_paths(mock_pool, mock_graph, mock_settings):
    mock_settings.correlation_learning_enabled = True
    # Patch build_samples, estimate_edges, write_estimates,
    # build_correlation_samples, estimate_correlation_edges, write_correlation_estimates
    with (
        patch("credibility.learning.run.build_samples", return_value=[]) as p1,
        patch("credibility.learning.run.estimate_edges", return_value=[]) as p2,
        patch("credibility.learning.run.write_estimates", return_value=2) as p3,
        patch("credibility.learning.run.build_correlation_samples", return_value=[]) as p4,
        patch("credibility.learning.run.estimate_correlation_edges", return_value=[]) as p5,
        patch("credibility.learning.run.write_correlation_estimates", return_value=1) as p6,
    ):
        total = await run_with(mock_pool, mock_graph, mock_settings)

    assert total == 3
    p1.assert_called_once()
    p4.assert_called_once()


async def test_run_with_skips_correlation_when_disabled(mock_pool, mock_graph, mock_settings):
    mock_settings.correlation_learning_enabled = False
    with (
        patch("credibility.learning.run.build_samples", return_value=[]),
        patch("credibility.learning.run.estimate_edges", return_value=[]),
        patch("credibility.learning.run.write_estimates", return_value=2),
        patch("credibility.learning.run.build_correlation_samples") as p_corr,
        patch("credibility.learning.run.estimate_correlation_edges"),
        patch("credibility.learning.run.write_correlation_estimates"),
    ):
        total = await run_with(mock_pool, mock_graph, mock_settings)

    assert total == 2
    p_corr.assert_not_called()


def test_learning_settings_correlation_enabled_default():
    settings = LearningSettings()
    assert settings.correlation_learning_enabled is True


def test_learning_settings_correlation_disabled_via_env(monkeypatch):
    monkeypatch.setenv(
        "CREDIBILITY_LEARNING_CORRELATION_LEARNING_ENABLED", "false"
    )
    settings = LearningSettings()
    assert settings.correlation_learning_enabled is False
```

## Acceptance criteria

1. `run_with()` calls both paths when `correlation_learning_enabled=True`.
2. `run_with()` skips `build_correlation_samples` when `correlation_learning_enabled=False`.
3. Return value is the sum of `written_causes + written_corr`.
4. `LearningSettings.correlation_learning_enabled` defaults to `True`.
5. Setting `CREDIBILITY_LEARNING_CORRELATION_LEARNING_ENABLED=false` disables the correlation path.
6. `app.py` requires no changes — `_run_learning` calls `run_with(ctx.pool, ctx.graph, LearningSettings())` unchanged.
7. All four unit tests pass.
8. All pre-existing learning tests still pass.
9. `mypy --strict` and `ruff check` clean.

## Definition of done

- [ ] `correlation_learning_enabled` field added to `LearningSettings`
- [ ] `run_with()` extended to call both paths sequentially
- [ ] Structured log message updated to include `written_causes` and `written_corr`
- [ ] Four unit tests added and passing
- [ ] `app.py` verified to require no changes
- [ ] No existing test broken
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
