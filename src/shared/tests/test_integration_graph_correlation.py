"""Integration tests for CORRELATES_WITH graph-client methods against a live Neo4j instance (E10).

Requires NEO4J_PASSWORD set in the environment (same guard used by prediction/test_integration.py).
The seed file infra/neo4j/init/08-seed-correlation-edges.cypher must have been applied before
running these tests (it is applied automatically when the container starts).
"""

from __future__ import annotations

import os

import pytest

from shared.graph import CausalGraphClient, Neo4jSettings
from shared.schemas.messages import AssetId, ConditionCode, Direction

pytestmark = pytest.mark.integration

NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")


@pytest.mark.skipif(NEO4J_PASSWORD is None, reason="NEO4J_PASSWORD not set")
async def test_get_correlation_edges_returns_seeded_edges() -> None:
    """get_correlation_edges finds the XOM_NYSE→NEM_NYSE UPSTREAM_UP edge from the seed file."""
    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()
    try:
        edges = await graph.get_correlation_edges(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP)
        target_ids = {e.target_asset_id for e in edges}
        assert AssetId.NEM_NYSE in target_ids, (
            "Expected NEM_NYSE in CORRELATES_WITH targets for XOM_NYSE / UPSTREAM_UP. "
            "Has 08-seed-correlation-edges.cypher been applied to the Neo4j container?"
        )
        nem_edge = next(e for e in edges if e.target_asset_id is AssetId.NEM_NYSE)
        # The specific direction is NOT asserted: Credibility's structure learner rewrites
        # `direction` and `weight` on this edge as it learns from outcomes (see
        # credibility/learning/seed_writer.py), so pinning the seeded value makes this fail once the
        # graph has evolved. What this test covers is that the query returns a well-formed edge.
        assert isinstance(nem_edge.direction, Direction)
        assert nem_edge.weight > 0.0
        assert nem_edge.alpha > 0.0
        assert nem_edge.beta > 0.0
    finally:
        await graph.close()


@pytest.mark.skipif(NEO4J_PASSWORD is None, reason="NEO4J_PASSWORD not set")
async def test_get_correlation_edges_filters_by_condition() -> None:
    """The condition WHERE clause actually filters: every seeded edge is UPSTREAM_UP, so a
    UPSTREAM_DOWN query must return nothing even though XOM_NYSE has outgoing edges."""
    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()
    try:
        # Sanity: XOM_NYSE does have UPSTREAM_UP edges, so an empty UPSTREAM_DOWN result proves
        # the filter works rather than just proving the asset has no edges at all.
        assert await graph.get_correlation_edges(AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP)
        assert await graph.get_correlation_edges(
            AssetId.XOM_NYSE, ConditionCode.UPSTREAM_DOWN
        ) == []
    finally:
        await graph.close()


@pytest.mark.skipif(NEO4J_PASSWORD is None, reason="NEO4J_PASSWORD not set")
async def test_update_and_read_back_correlation_weight() -> None:
    """update_correlation_weight persists alpha/beta; get_correlation_edge_counts reads it back."""
    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()
    try:
        # Read the current counts so we can restore them afterward.
        original = await graph.get_correlation_edge_counts(
            AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP
        )
        assert original is not None, (
            "XOM_NYSE→NEM_NYSE UPSTREAM_UP edge not found. "
            "Has 08-seed-correlation-edges.cypher been applied?"
        )
        alpha_orig, beta_orig = original

        # Write new counts.
        new_alpha = alpha_orig + 1.0
        new_beta = beta_orig + 0.5
        await graph.update_correlation_weight(
            AssetId.XOM_NYSE,
            AssetId.NEM_NYSE,
            ConditionCode.UPSTREAM_UP,
            alpha=new_alpha,
            beta=new_beta,
        )

        # Read back and verify.
        updated = await graph.get_correlation_edge_counts(
            AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP
        )
        assert updated is not None
        assert updated == pytest.approx((new_alpha, new_beta))

        # Restore to original so repeated test runs stay idempotent.
        await graph.update_correlation_weight(
            AssetId.XOM_NYSE,
            AssetId.NEM_NYSE,
            ConditionCode.UPSTREAM_UP,
            alpha=alpha_orig,
            beta=beta_orig,
        )
    finally:
        await graph.close()
