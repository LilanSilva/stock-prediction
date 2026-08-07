# T02 — `build_correlation_samples()` in `dataset.py`

## Context

The existing `build_samples()` in `dataset.py` reads `cleansing.events` and
`market_data.close_observations`. The correlation learner needs a different query: it must
find direct (`propagation_depth=0`) predictions that have been scored, then for each such
prediction look up the **target asset's** actual price move in the same settlement session.

This function is added to the same `dataset.py` file alongside `build_samples`. It shares the
Postgres pool but issues its own queries.

## The join logic in plain terms

For each scored direct prediction of `source_asset` in direction `D` within `lookback_days`:

1. Determine the settlement session date (the "after" close) — already stored in
   `verification.scores.scored_at` or derivable from `verification.evaluations.settlement_session`.
2. For every `CORRELATES_WITH` edge that has `source_asset` as its source (read from Neo4j
   or inferred from the seed — see design note below), look up the target asset's close on
   that same settlement session from `market_data.close_observations`.
3. Also look up the target asset's baseline close (one session before settlement) to compute
   `actual_return = (settlement - baseline) / baseline`.
4. Emit one `CorrelationSample(source_asset, condition, target_asset, actual_return)`.

## Design note: where to get the target assets

Two options:

- **Option A — from the graph (preferred):** call `graph.get_correlation_edges(source_asset,
  condition)` for each scored prediction to get its declared target assets. This means only
  asset pairs that already have a seeded or previously learned edge are sampled. New edges
  can only be discovered if the expert first seeds them.
- **Option B — from the data:** compare all asset pairs that co-moved in the same session and
  infer new edges purely from price correlation. This can discover truly new pairs but risks
  spurious correlations and requires many more samples to be reliable.

**Use Option A for now.** It is consistent with how `build_samples` only builds samples for
event types that already have `CAUSES` edges — the learner refines known edges and promotes
strong ones, it does not blindly mine all combinations.

## SQL queries to add to `dataset.py`

```python
# Direct scored predictions within the lookback window.
# Cross-schema read: prediction and verification schemas.
_DIRECT_SCORED_PREDICTIONS_QUERY = """
SELECT
    p.prediction_id,
    p.asset_id         AS source_asset_id,
    p.direction        AS predicted_direction,
    p.decision_at,
    e.settlement_session,
    e.baseline_session
FROM prediction.predictions p
JOIN verification.evaluations e ON e.prediction_id = p.prediction_id
WHERE p.status          = 'PENDING'          -- status is PENDING until superseded; scores exist
  AND p.decision_at    >= $1                 -- within lookback window
  AND p.direction      != 'NEUTRAL'          -- only directional predictions carry a condition
  AND (
      SELECT COUNT(*) FROM verification.scores s
      WHERE s.prediction_id = p.prediction_id
  ) > 0                                      -- must be scored
ORDER BY p.decision_at
"""
```

Wait — read `prediction.predictions` DDL again. The `status` column is `DEFAULT 'PENDING'`
and is never updated to `SCORED` by the Prediction Service itself — scoring is owned by
Verification. The reliable join is:

```python
_DIRECT_SCORED_PREDICTIONS_QUERY = """
SELECT
    p.prediction_id,
    p.asset_id              AS source_asset_id,
    p.direction             AS predicted_direction,
    p.decision_at,
    e.settlement_session,
    e.baseline_session
FROM prediction.predictions  p
JOIN verification.evaluations e USING (prediction_id)
JOIN verification.scores      s USING (prediction_id)
WHERE p.decision_at >= $1
  AND p.direction  != 'NEUTRAL'
ORDER BY p.decision_at
"""
```

This inner-joins `verification.scores` — only predictions that have been scored are returned.
No `propagation_depth` column exists in `prediction.predictions` yet (it is stored in the
`PredictionMade` payload in `prediction.outbox_events`). To restrict to direct predictions,
filter on the outbox payload:

```python
_DIRECT_SCORED_PREDICTIONS_QUERY = """
SELECT
    p.prediction_id,
    p.asset_id                                          AS source_asset_id,
    p.direction                                         AS predicted_direction,
    e.settlement_session,
    e.baseline_session
FROM prediction.predictions  p
JOIN prediction.outbox_events o ON o.aggregate_id = p.prediction_id
JOIN verification.evaluations e USING (prediction_id)
JOIN verification.scores      s USING (prediction_id)
WHERE p.decision_at >= $1
  AND p.direction  != 'NEUTRAL'
  AND (o.payload::jsonb ->> 'propagation_depth')::int = 0
ORDER BY p.decision_at
"""
```

Note: `prediction.outbox_events.aggregate_id` is the `prediction_id` and `payload` holds the
`PredictionMade` JSON. Verify this join against the actual outbox DDL before using it —
read `prediction/db.py` section for `outbox_events` to confirm `aggregate_id` is indeed the
`prediction_id` foreign key. If the payload join is unreliable, a fallback is to add a
`propagation_depth` column to `prediction.predictions` table DDL (a separate DDL task,
low cost).

## Function signature and implementation

Add to `src/services/credibility/credibility/learning/dataset.py`:

```python
async def build_correlation_samples(
    pool: asyncpg.Pool,
    graph: CausalGraphClient,
    *,
    lookback_days: int,
    volatility_lookback_days: int = 30,
    abnormal_threshold: float = 2.0,
) -> list[CorrelationSample]:
    """Build correlation samples from scored direct predictions within lookback_days.

    For each scored direct prediction of source_asset in direction D, queries
    CORRELATES_WITH edges for that asset and condition, then looks up the target
    asset's actual return in the same settlement session. Emits one CorrelationSample
    per (source_asset, condition, target_asset) observation.

    Cross-schema reads: prediction.*, verification.*, market_data.* — same exception
    as the existing build_samples (offline analytics batch, never mutates those schemas).
    """
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    volatility_cutoff = (datetime.now(UTC) - timedelta(days=volatility_lookback_days)).date()
    samples: list[CorrelationSample] = []
    close_cache: dict[tuple[str, date], float | None] = {}
    volatility_cache: dict[str, float] = {}
    skipped = 0

    async with pool.acquire() as conn:
        rows = await conn.fetch(_DIRECT_SCORED_PREDICTIONS_QUERY, cutoff)
        for row in rows:
            source_asset = AssetId(row["source_asset_id"])
            direction = Direction(row["predicted_direction"])
            condition = (
                ConditionCode.UPSTREAM_UP
                if direction is Direction.UP
                else ConditionCode.UPSTREAM_DOWN
            )
            settlement: date = row["settlement_session"]
            baseline: date = row["baseline_session"]

            # Get all CORRELATES_WITH targets for this source + condition.
            corr_edges = await graph.get_correlation_edges(source_asset, condition)
            if not corr_edges:
                continue

            for edge in corr_edges:
                target = edge.target_asset_id
                try:
                    series = resolve(target)
                except UnknownAssetError:
                    logger.warning("corr_learning_skip_unknown_asset", asset_id=str(target))
                    continue

                baseline_close = await _load_close(conn, close_cache, target, baseline)
                settlement_close = await _load_close(conn, close_cache, target, settlement)
                if baseline_close is None or settlement_close is None:
                    skipped += 1
                    continue

                actual_return = (settlement_close - baseline_close) / baseline_close
                volatility = await _load_volatility(
                    conn, volatility_cache, target, volatility_cutoff
                )
                is_abnormal = (
                    volatility > 0.0
                    and abs(actual_return) >= abnormal_threshold * volatility
                )
                samples.append(
                    CorrelationSample(
                        source_asset=source_asset,
                        condition=condition,
                        target_asset=target,
                        actual_return=actual_return,
                        is_abnormal=is_abnormal,
                        asset_volatility=volatility,
                    )
                )

    logger.info(
        "corr_learning_samples_built",
        predictions=len(rows),
        samples=len(samples),
        skipped_missing_price=skipped,
    )
    return samples
```

The helper functions `_load_close`, `_load_volatility`, `_calculate_volatility` already exist
in `dataset.py` — reuse them directly, do not copy them.

## Unit tests to add

**File:** `src/services/credibility/tests/test_learning_dataset.py` — add to the existing
file, do not create a new one.

```python
async def test_build_correlation_samples_empty_when_no_rows(mock_pool, mock_graph):
    # No scored direct predictions in the window → empty list, no exception.
    mock_pool.acquire().__aenter__.return_value.fetch = AsyncMock(return_value=[])
    result = await build_correlation_samples(
        mock_pool, mock_graph, lookback_days=30
    )
    assert result == []


async def test_build_correlation_samples_skips_when_no_corr_edges(mock_pool, mock_graph):
    # Prediction row exists but graph returns no CORRELATES_WITH edges → sample skipped.
    mock_pool.acquire().__aenter__.return_value.fetch = AsyncMock(
        return_value=[make_scored_prediction_row(
            asset_id="XOM_NYSE", direction="UP",
            settlement_session=date(2026, 1, 2),
            baseline_session=date(2026, 1, 1),
        )]
    )
    mock_graph.get_correlation_edges = AsyncMock(return_value=[])
    result = await build_correlation_samples(mock_pool, mock_graph, lookback_days=30)
    assert result == []


async def test_build_correlation_samples_produces_sample(mock_pool, mock_graph, mock_closes):
    # One prediction, one correlation edge, closes available → one sample emitted.
    mock_pool.acquire().__aenter__.return_value.fetch = AsyncMock(
        return_value=[make_scored_prediction_row(
            asset_id="XOM_NYSE", direction="UP",
            settlement_session=date(2026, 1, 2),
            baseline_session=date(2026, 1, 1),
        )]
    )
    mock_graph.get_correlation_edges = AsyncMock(return_value=[
        make_correlation_edge(AssetId.XOM_NYSE, AssetId.NEM_NYSE,
                              ConditionCode.UPSTREAM_UP, Direction.DOWN, 0.45)
    ])
    mock_closes.return_value = {
        ("NEM_NYSE", date(2026, 1, 1)): 50.0,
        ("NEM_NYSE", date(2026, 1, 2)): 49.0,
    }
    result = await build_correlation_samples(mock_pool, mock_graph, lookback_days=30)
    assert len(result) == 1
    assert result[0].source_asset == AssetId.XOM_NYSE
    assert result[0].condition == ConditionCode.UPSTREAM_UP
    assert result[0].target_asset == AssetId.NEM_NYSE
    assert result[0].actual_return == pytest.approx(-0.02)
```

## Acceptance criteria

1. `build_correlation_samples` returns an empty list when no scored direct predictions exist
   in the window — no exception raised.
2. A prediction with no `CORRELATES_WITH` edges produces no samples.
3. A prediction with one edge and both closes available produces one `CorrelationSample` with
   the correct `actual_return` for the **target** asset.
4. `condition` is `UPSTREAM_UP` when `predicted_direction == UP`, `UPSTREAM_DOWN` when `DOWN`.
5. Missing closes for the target asset are skipped (logged, not raised).
6. All three unit tests pass.
7. All pre-existing `test_learning_dataset.py` tests still pass.
8. `mypy --strict` and `ruff check` clean.

## Definition of done

- [ ] `_DIRECT_SCORED_PREDICTIONS_QUERY` SQL added and join verified against real DDL
- [ ] `build_correlation_samples()` implemented, reusing `_load_close` and `_load_volatility`
- [ ] Three unit tests added and passing
- [ ] No existing test broken
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
