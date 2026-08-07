# T03 — `CausalGraphClient`: `get_correlation_edges` and `update_correlation_weight`

## Context

`CausalGraphClient` in `src/shared/shared/graph/client.py` only queries `CAUSES` edges.
The Prediction Service (S03) needs to query `CORRELATES_WITH` edges for a given source asset
and active condition. The Credibility Service (S04) needs to update the `alpha`/`beta` counts
on those edges after a propagated prediction is scored.

Two new public methods are added. The existing methods are not changed.

## New model: `CorrelationEdge`

Add to `src/shared/shared/graph/models.py`:

```python
class CorrelationEdge(BaseModel):
    """A single (:Asset)-[:CORRELATES_WITH {condition}]->(:Asset) edge that is active.

    ``weight`` is the expert-assigned magnitude in [0,1]; sign is carried by ``direction``.
    ``alpha``/``beta`` are the Beta-Bernoulli reliability counts (seeded 1.0/1.0).
    ``condition`` is always set (unlike CAUSES, CORRELATES_WITH has no unconditional form).
    """
    model_config = ConfigDict(frozen=True)

    source_asset_id: AssetId
    target_asset_id: AssetId
    condition: ConditionCode
    direction: Direction
    weight: Annotated[float, Field(ge=0.0, le=1.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    alpha: Annotated[float, Field(gt=0.0)]
    beta: Annotated[float, Field(gt=0.0)]

    @property
    def reliability(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def edge_id(self) -> str:
        return f"{self.source_asset_id.value}|{self.condition.value}->{self.target_asset_id.value}"
```

Export `CorrelationEdge` from `src/shared/shared/graph/__init__.py` alongside `FiringEdge`.

## New Cypher queries

Add to `src/shared/shared/graph/client.py` as module-level constants (alongside
`_FIRING_EDGES_CYPHER`):

```python
_CORRELATION_EDGES_CYPHER = """
MATCH (a1:Asset {id: $source_asset_id})-[r:CORRELATES_WITH]->(a2:Asset)
WHERE r.condition = $condition
RETURN a1.id AS source_asset_id, a2.id AS target_asset_id,
       r.direction AS direction, r.weight AS weight, r.confidence AS confidence,
       r.alpha AS alpha, r.beta AS beta, r.condition AS condition
"""

_UPDATE_CORRELATION_EDGE_CYPHER = """
MATCH (a1:Asset {id: $source_asset_id})-[r:CORRELATES_WITH {condition: $condition}]->
      (a2:Asset {id: $target_asset_id})
SET r.alpha = $alpha, r.beta = $beta, r.last_updated = datetime()
RETURN r.alpha AS alpha, r.beta AS beta
"""
```

## New methods on `CausalGraphClient`

Add to the `CausalGraphClient` class:

```python
async def get_correlation_edges(
    self,
    source_asset_id: AssetId,
    condition: ConditionCode,
) -> list[CorrelationEdge]:
    """Return CORRELATES_WITH edges from source_asset_id active under condition.

    Used by the Prediction Service propagation pass to find downstream assets that
    should receive a secondary prediction when source_asset_id is predicted in the
    direction implied by condition (UPSTREAM_UP or UPSTREAM_DOWN).
    """
    driver = self._require_driver()
    params = {
        "source_asset_id": source_asset_id.value,
        "condition": condition.value,
    }
    try:
        async with driver.session() as session:
            result = await session.run(_CORRELATION_EDGES_CYPHER, params)
            records = await result.data()
    except Exception as exc:
        raise GraphTransportError(
            f"neo4j correlation-edge query failed: {exc}"
        ) from exc

    edges: list[CorrelationEdge] = []
    for row in cast(list[dict[str, Any]], records):
        edges.append(
            CorrelationEdge(
                source_asset_id=AssetId(row["source_asset_id"]),
                target_asset_id=AssetId(row["target_asset_id"]),
                condition=ConditionCode(row["condition"]),
                direction=Direction(row["direction"]),
                weight=float(row["weight"]),
                confidence=float(row["confidence"]),
                alpha=float(row["alpha"]),
                beta=float(row["beta"]),
            )
        )
    return edges


async def update_correlation_weight(
    self,
    source_asset_id: AssetId,
    target_asset_id: AssetId,
    condition: ConditionCode,
    *,
    alpha: float,
    beta: float,
) -> None:
    """Persist Beta-Bernoulli counts for a CORRELATES_WITH edge (used by Credibility).

    Raises GraphTransportError when the edge does not exist — callers must only update
    edges they know were seeded or created by the offline learner.
    """
    driver = self._require_driver()
    params: dict[str, Any] = {
        "source_asset_id": source_asset_id.value,
        "target_asset_id": target_asset_id.value,
        "condition": condition.value,
        "alpha": alpha,
        "beta": beta,
    }
    edge_label = (
        f"{source_asset_id.value}|{condition.value}->{target_asset_id.value}"
    )
    try:
        async with driver.session() as session:
            result = await session.run(_UPDATE_CORRELATION_EDGE_CYPHER, params)
            updated = await result.single()
    except Exception as exc:
        raise GraphTransportError(
            f"neo4j correlation-edge update failed: {exc}"
        ) from exc
    if updated is None:
        raise GraphTransportError(
            f"no CORRELATES_WITH edge for {edge_label}"
        )
```

## Unit tests to add

**File:** `src/shared/tests/test_graph_client.py` (check if it exists; add to it rather than
creating a new file).

These tests mock the Neo4j driver using `AsyncMock` — they must not require a live database.

```python
async def test_get_correlation_edges_returns_empty_when_none(mock_driver):
    # Arrange: session returns no rows
    mock_driver.session().__aenter__.return_value.run.return_value.data = AsyncMock(
        return_value=[]
    )
    client = CausalGraphClient(settings=fake_settings)
    client._driver = mock_driver

    edges = await client.get_correlation_edges(
        AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP
    )
    assert edges == []


async def test_get_correlation_edges_parses_row(mock_driver):
    row = {
        "source_asset_id": "XOM_NYSE", "target_asset_id": "NEM_NYSE",
        "condition": "UPSTREAM_UP", "direction": "DOWN",
        "weight": 0.45, "confidence": 0.60, "alpha": 1.0, "beta": 1.0,
    }
    mock_driver.session().__aenter__.return_value.run.return_value.data = AsyncMock(
        return_value=[row]
    )
    client = CausalGraphClient(settings=fake_settings)
    client._driver = mock_driver

    edges = await client.get_correlation_edges(
        AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP
    )
    assert len(edges) == 1
    assert edges[0].target_asset_id == AssetId.NEM_NYSE
    assert edges[0].direction == Direction.DOWN
    assert round(edges[0].reliability, 4) == 0.5


async def test_update_correlation_weight_raises_when_edge_missing(mock_driver):
    mock_driver.session().__aenter__.return_value.run.return_value.single = AsyncMock(
        return_value=None
    )
    client = CausalGraphClient(settings=fake_settings)
    client._driver = mock_driver

    with pytest.raises(GraphTransportError, match="no CORRELATES_WITH edge"):
        await client.update_correlation_weight(
            AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP,
            alpha=2.0, beta=1.0,
        )


def test_correlation_edge_reliability():
    edge = CorrelationEdge(
        source_asset_id=AssetId.XOM_NYSE, target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP, direction=Direction.DOWN,
        weight=0.45, confidence=0.60, alpha=3.0, beta=1.0,
    )
    assert edge.reliability == pytest.approx(0.75)


def test_correlation_edge_id_format():
    edge = CorrelationEdge(
        source_asset_id=AssetId.XOM_NYSE, target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP, direction=Direction.DOWN,
        weight=0.45, confidence=0.60, alpha=1.0, beta=1.0,
    )
    assert edge.edge_id == "XOM_NYSE|UPSTREAM_UP->NEM_NYSE"
```

## Acceptance criteria

1. `CorrelationEdge` is importable from `shared.graph`.
2. `client.get_correlation_edges(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP)` calls Neo4j
   with `source_asset_id="XOM_NYSE"` and `condition="UPSTREAM_UP"`.
3. `client.get_correlation_edges(...)` returns an empty list when Neo4j returns no rows (no
   exception raised).
4. `client.update_correlation_weight(...)` raises `GraphTransportError` when the edge is not
   found in Neo4j.
5. `CorrelationEdge.reliability` returns `alpha / (alpha + beta)`.
6. `CorrelationEdge.edge_id` returns `"SOURCE|CONDITION->TARGET"`.
7. All five new unit tests pass.
8. All pre-existing `CausalGraphClient` tests still pass.
9. `mypy --strict` and `ruff check` clean.

## Definition of done

- [ ] `CorrelationEdge` model added to `shared/graph/models.py`
- [ ] `CorrelationEdge` exported from `shared/graph/__init__.py`
- [ ] `_CORRELATION_EDGES_CYPHER` and `_UPDATE_CORRELATION_EDGE_CYPHER` added as module constants
- [ ] `get_correlation_edges` and `update_correlation_weight` methods added to `CausalGraphClient`
- [ ] Five unit tests added and passing
- [ ] No existing test broken
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
