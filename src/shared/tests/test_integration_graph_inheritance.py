"""Integration tests for group-edge inheritance against a live Neo4j instance (E12 S02).

Requires NEO4J_PASSWORD set in the environment, and the seed files under infra/neo4j/init/ applied
(the container does that on start).

Why these are integration tests rather than unit tests: the inheritance rule lives entirely in a
Cypher `NOT EXISTS` predicate. The unit suite stubs the driver, so it can only assert that the
string
"NOT EXISTS" appears in the query — which it did while the predicate was wrong. A learned per-asset
conditioned edge failed to suppress the unconditional group edge, both fired, and one causal factor
was
counted twice. Only a real graph can catch that, so this file exists to catch it.
"""

from __future__ import annotations

import os

import pytest

from shared.graph import CausalGraphClient, Neo4jSettings
from shared.schemas.messages import AssetId, ConditionCode, EventType

pytestmark = pytest.mark.integration

NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")

requires_neo4j = pytest.mark.skipif(NEO4J_PASSWORD is None, reason="NEO4J_PASSWORD not set")


@pytest.fixture
async def graph():
    client = CausalGraphClient(Neo4jSettings())
    await client.connect()
    yield client
    await client.close()


@requires_neo4j
async def test_one_factor_fires_at_most_one_edge_per_asset(graph: CausalGraphClient) -> None:
    """No (factor, asset) pair may fire twice for one condition set.

    This is the invariant that matters for `decide()`: forces are summed per asset, so a factor
    counted twice inflates both the confidence mass and the magnitude average.
    """
    conditions = [
        {ConditionCode.SAFE_HAVEN_ONLY},
        {ConditionCode.TRANSPORT_AFFECTED},
        {ConditionCode.SAFE_HAVEN_ONLY, ConditionCode.TRANSPORT_AFFECTED},
    ]
    for event_type in (
        EventType.MILITARY_CONFLICT,
        EventType.SUPPLY_DISRUPTION,
        EventType.STRAIT_CLOSURE,
        EventType.SANCTIONS,
        EventType.INFLATION_CHANGE,
    ):
        for condition_set in conditions:
            edges = await graph.get_firing_edges(event_type, conditions=condition_set)
            seen: dict[AssetId, list[str]] = {}
            for edge in edges:
                seen.setdefault(edge.asset_id, []).append(edge.edge_id)
            duplicated = {a: ids for a, ids in seen.items() if len(ids) > 1}
            assert not duplicated, (
                f"{event_type.value} with {sorted(c.value for c in condition_set)} fired more than "
                f"one edge for: {duplicated}"
            )


@requires_neo4j
async def test_own_edge_suppresses_the_group_edge(graph: CausalGraphClient) -> None:
    """When an asset has its own eligible edge, no inherited edge is returned for that factor.

    The documented intent (infra/neo4j/init/06 header): a member predicts from its group's prior
    only
    "until the offline learner writes company-specific ones".
    """
    edges = await graph.get_firing_edges(
        EventType.MILITARY_CONFLICT,
        [AssetId.NEM_NYSE],
        conditions={ConditionCode.SAFE_HAVEN_ONLY},
    )
    if not any(e.inherited_from is None for e in edges):
        pytest.skip("NEM_NYSE has no own MILITARY_CONFLICT edge in this graph")
    assert all(e.inherited_from is None for e in edges), (
        "an inherited edge was returned alongside the asset's own edge: "
        f"{[e.edge_id for e in edges]}"
    )


@requires_neo4j
async def test_group_edge_still_fires_for_a_condition_the_asset_has_no_edge_for(
    graph: CausalGraphClient,
) -> None:
    """Suppression must not starve an asset under a condition it has no own edge for.

    The override is scoped to the active condition set, so an asset carrying only a SAFE_HAVEN_ONLY
    edge still inherits its group's prior under TRANSPORT_AFFECTED.
    """
    edges = await graph.get_firing_edges(
        EventType.MILITARY_CONFLICT,
        [AssetId.NEM_NYSE],
        conditions={ConditionCode.TRANSPORT_AFFECTED},
    )
    assert edges, "NEM_NYSE fired nothing for a transport-affected conflict"


@requires_neo4j
async def test_distant_conflict_does_not_move_the_oil_proxy(graph: CausalGraphClient) -> None:
    """ADR-006's core rule, asserted against the real graph.

    A conflict with no transport cue is a safe-haven bid for gold; oil is unaffected. This is the
    behaviour E12 defect 4 questioned — it holds, because 06 conditions the OIL_GAS edge even though
    04/05's commodity-level edges never loaded.
    """
    oil = await graph.get_firing_edges(
        EventType.MILITARY_CONFLICT,
        [AssetId.XOM_NYSE],
        conditions={ConditionCode.SAFE_HAVEN_ONLY},
    )
    assert oil == [], f"a distant conflict fired an oil edge: {[e.edge_id for e in oil]}"

    gold = await graph.get_firing_edges(
        EventType.MILITARY_CONFLICT,
        [AssetId.NEM_NYSE],
        conditions={ConditionCode.SAFE_HAVEN_ONLY},
    )
    assert gold, "a distant conflict fired no gold edge"


@requires_neo4j
async def test_transport_affected_conflict_does_move_the_oil_proxy(
    graph: CausalGraphClient,
) -> None:
    edges = await graph.get_firing_edges(
        EventType.MILITARY_CONFLICT,
        [AssetId.XOM_NYSE],
        conditions={ConditionCode.TRANSPORT_AFFECTED},
    )
    assert edges, "a transport-affected conflict fired no oil edge"


@requires_neo4j
async def test_no_seeded_edge_targets_a_missing_node(graph: CausalGraphClient) -> None:
    """GOLD and BRENT_OIL are not registry assets, so nothing may depend on them.

    infra/neo4j/init/04 and 05 targeted those ids for months; every MATCH bound nothing and every
    MERGE
    was skipped, silently. This asserts the cleanup held.
    """
    driver = graph._require_driver()  # noqa: SLF001 - no public read-through for an ad-hoc query
    async with driver.session() as session:
        result = await session.run(
            "MATCH (n) WHERE n.id IN ['GOLD', 'BRENT_OIL'] RETURN n.id AS id"
        )
        rows = await result.data()
    assert rows == [], f"unexpected commodity nodes present: {rows}"
