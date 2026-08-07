# T04 — `write_correlation_estimates()` and `upsert_correlation_edge` on Graph Client

## Context

`seed_writer.py` currently calls `graph.upsert_conditioned_edge()` which `MERGE`s a
`(:CausalFactor)-[:CAUSES {condition}]->(:Asset)` edge. There is no equivalent for
`(:Asset)-[:CORRELATES_WITH {condition}]->(:Asset)`.

This task adds:

1. `upsert_correlation_edge()` to `CausalGraphClient` in `shared/graph/client.py`
2. `write_correlation_estimates()` to `seed_writer.py`

## Part 1 — `upsert_correlation_edge` on `CausalGraphClient`

**Before writing**, read `src/shared/shared/graph/client.py` and check whether
`upsert_correlation_edge` was already added by S02 T03. If it exists, skip Part 1.

### Cypher query to add to `client.py`

```python
_UPSERT_CORRELATION_EDGE_CYPHER = """
MERGE (a1:Asset {id: $source_asset_id})
MERGE (a2:Asset {id: $target_asset_id})
MERGE (a1)-[r:CORRELATES_WITH {condition: $condition}]->(a2)
SET r.direction    = $direction,
    r.weight       = $weight,
    r.confidence   = $confidence,
    r.alpha        = $alpha,
    r.beta         = $beta,
    r.last_updated = datetime()
RETURN r.alpha AS alpha, r.beta AS beta
"""
```

### Method to add to `CausalGraphClient`

```python
async def upsert_correlation_edge(
    self,
    source_asset_id: AssetId,
    condition: ConditionCode,
    target_asset_id: AssetId,
    *,
    direction: Direction,
    weight: float,
    confidence: float,
    alpha: float,
    beta: float,
) -> None:
    """Create or refine a CORRELATES_WITH edge (used by the offline structure learner).

    Idempotent MERGE: keeps expert-seeded edges as the prior and overwrites their statistics
    with data-derived values. Creates Asset nodes if absent (they should exist from seed,
    but MERGE is safe either way).
    """
    driver = self._require_driver()
    params = {
        "source_asset_id": source_asset_id.value,
        "target_asset_id": target_asset_id.value,
        "condition": condition.value,
        "direction": direction.value,
        "weight": weight,
        "confidence": confidence,
        "alpha": alpha,
        "beta": beta,
    }
    try:
        async with driver.session() as session:
            await session.run(_UPSERT_CORRELATION_EDGE_CYPHER, params)
    except Exception as exc:
        raise GraphTransportError(
            f"neo4j correlation-edge upsert failed: {exc}"
        ) from exc
```

Export `upsert_correlation_edge` is already covered by the public class — no `__init__.py`
change needed.

## Part 2 — `write_correlation_estimates()` in `seed_writer.py`

### Protocol extension

The existing `EdgeUpserter` protocol in `seed_writer.py` declares only `upsert_conditioned_edge`.
Add a parallel protocol for correlation writes, or extend `EdgeUpserter`. Prefer a separate
protocol to avoid breaking the existing `write_estimates` signature:

```python
class CorrelationEdgeUpserter(Protocol):
    """The subset of CausalGraphClient the correlation writer depends on."""

    async def upsert_correlation_edge(
        self,
        source_asset_id: AssetId,
        condition: ConditionCode,
        target_asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> None: ...
```

### Writer function

```python
async def write_correlation_estimates(
    graph: CorrelationEdgeUpserter,
    estimates: list[CorrelationEdgeEstimate],
) -> int:
    """Upsert each non-NEUTRAL correlation estimate; return the number of edges written."""
    written = 0
    for estimate in estimates:
        if estimate.direction is Direction.NEUTRAL:
            continue
        await graph.upsert_correlation_edge(
            estimate.source_asset,
            estimate.condition,
            estimate.target_asset,
            direction=estimate.direction,
            weight=estimate.weight,
            confidence=estimate.confidence,
            alpha=estimate.alpha,
            beta=estimate.beta,
        )
        written += 1
    logger.info(
        "corr_learning_edges_written", written=written, estimates=len(estimates)
    )
    return written
```

## Unit tests to add

### `test_graph_client.py` (for `upsert_correlation_edge`)

```python
async def test_upsert_correlation_edge_calls_cypher(mock_driver):
    client = CausalGraphClient(settings=fake_settings)
    client._driver = mock_driver

    await client.upsert_correlation_edge(
        AssetId.XOM_NYSE,
        ConditionCode.UPSTREAM_UP,
        AssetId.NEM_NYSE,
        direction=Direction.DOWN,
        weight=0.45,
        confidence=0.60,
        alpha=2.0,
        beta=1.0,
    )
    mock_driver.session().__aenter__.return_value.run.assert_called_once()
    call_args = mock_driver.session().__aenter__.return_value.run.call_args
    params = call_args[0][1]
    assert params["source_asset_id"] == "XOM_NYSE"
    assert params["target_asset_id"] == "NEM_NYSE"
    assert params["condition"] == "UPSTREAM_UP"
    assert params["direction"] == "DOWN"
```

### `test_learning_seed_writer.py` (for `write_correlation_estimates`)

```python
async def test_write_correlation_estimates_skips_neutral():
    graph = AsyncMock(spec=CorrelationEdgeUpserter)
    estimates = [
        CorrelationEdgeEstimate(
            source_asset=AssetId.XOM_NYSE,
            condition=ConditionCode.UPSTREAM_UP,
            target_asset=AssetId.NEM_NYSE,
            direction=Direction.NEUTRAL,
            weight=0.1, confidence=0.5, alpha=1.0, beta=1.0, sample_count=5,
        )
    ]
    written = await write_correlation_estimates(graph, estimates)
    assert written == 0
    graph.upsert_correlation_edge.assert_not_called()


async def test_write_correlation_estimates_calls_upsert():
    graph = AsyncMock(spec=CorrelationEdgeUpserter)
    estimates = [
        CorrelationEdgeEstimate(
            source_asset=AssetId.XOM_NYSE,
            condition=ConditionCode.UPSTREAM_UP,
            target_asset=AssetId.NEM_NYSE,
            direction=Direction.DOWN,
            weight=0.45, confidence=0.70, alpha=5.0, beta=2.0, sample_count=6,
        )
    ]
    written = await write_correlation_estimates(graph, estimates)
    assert written == 1
    graph.upsert_correlation_edge.assert_called_once_with(
        AssetId.XOM_NYSE,
        ConditionCode.UPSTREAM_UP,
        AssetId.NEM_NYSE,
        direction=Direction.DOWN,
        weight=0.45,
        confidence=0.70,
        alpha=5.0,
        beta=2.0,
    )


async def test_write_correlation_estimates_empty_input():
    graph = AsyncMock(spec=CorrelationEdgeUpserter)
    written = await write_correlation_estimates(graph, [])
    assert written == 0
```

## Acceptance criteria

1. `upsert_correlation_edge` is callable on `CausalGraphClient` and issues the `MERGE`
   Cypher with correct parameters.
2. `write_correlation_estimates` skips `NEUTRAL` estimates — `upsert_correlation_edge` is
   never called for them.
3. `write_correlation_estimates` calls `upsert_correlation_edge` once per non-NEUTRAL estimate.
4. Empty input returns `0` with no exception.
5. All four unit tests pass.
6. All pre-existing `test_learning_seed_writer.py` and `test_graph_client.py` tests pass.
7. `mypy --strict` and `ruff check` clean on all changed files.

## Definition of done

- [ ] `_UPSERT_CORRELATION_EDGE_CYPHER` added to `client.py`
- [ ] `upsert_correlation_edge` method added to `CausalGraphClient`
- [ ] `CorrelationEdgeUpserter` protocol added to `seed_writer.py`
- [ ] `write_correlation_estimates()` added to `seed_writer.py`
- [ ] Four unit tests added and passing
- [ ] No existing test broken
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
