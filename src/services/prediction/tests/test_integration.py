from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from shared.graph import CausalGraphClient, Neo4jSettings
from shared.messaging.client import RabbitMQClient
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    EventDetected,
    EventType,
    ExtractionMethod,
)

from prediction.config import PredictionSettings
from prediction.context import window_bounds
from prediction.db import apply_schema, create_pool
from prediction.outbox import PredictionOutboxPublisher
from prediction.pipeline import PredictionPipeline
from prediction.repository import PredictionRepository

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")


class _StubPriceReader:
    """No-network price reader; these integration events are OCCURRENCE, so elevation is moot."""

    async def is_elevated(self, asset_id: AssetId) -> bool:
        return False

    async def is_price_available(self, asset_id: AssetId) -> bool:
        return True


def _event(
    asset: AssetId,
    event_type: EventType,
    context_tags: list[ConditionCode] | None = None,
) -> EventDetected:
    """Build an EventDetected for the live-stack tests.

    ``context_tags`` matters: the seeded ``MILITARY_CONFLICT`` edges are all **conditioned**
    (`05-seed-conditioned-edges.cypher` deletes the blanket edge as superseded per ADR-006), and
    `PRD-12` fires a conditioned edge only when its condition is in the active set. A bare event
    with no tags therefore has no firing edge and correctly produces no prediction.
    """
    now = datetime.now(UTC)
    return EventDetected(
        correlation_id=uuid.uuid4(),
        occurred_at=now,
        event_id=uuid.uuid4(),
        cluster_id=uuid.uuid4(),
        canonical_summary="itest event",
        event_type=event_type,
        affected_asset_ids=[asset],
        context_tags=context_tags or [],
        first_seen_at=now,
        last_seen_at=now,
        extraction_method=ExtractionMethod.LOCAL,
    )


async def _isolate_stance(pool: asyncpg.Pool, asset_id: AssetId) -> list[uuid.UUID]:
    """Park the asset's active predictions so stance rules cannot suppress this test's decision.

    `PRD-31` marks a context PREDICTED and emits nothing when the new decision matches the latest
    active prediction on (direction, magnitude). Against a live database the asset already carries
    real PENDING predictions, so a test asserting "a prediction was produced" would fail on correct
    behaviour. Returns the parked ids so the test can restore them.
    """
    rows = await pool.fetch(
        "UPDATE prediction.predictions SET status = 'WITHDRAWN' "
        "WHERE asset_id = $1 AND status = 'PENDING' RETURNING prediction_id",
        asset_id.value,
    )
    return [r["prediction_id"] for r in rows]


async def _restore_stance(pool: asyncpg.Pool, prediction_ids: list[uuid.UUID]) -> None:
    """Undo _isolate_stance so the live pipeline's own state is left exactly as it was."""
    if not prediction_ids:
        return
    await pool.execute(
        "UPDATE prediction.predictions SET status = 'PENDING' WHERE prediction_id = ANY($1::uuid[])",
        prediction_ids,
    )


async def _cleanup(pool: asyncpg.Pool, context_ids: list[uuid.UUID]) -> None:
    for context_id in context_ids:
        await pool.execute(
            "DELETE FROM prediction.outbox_events WHERE aggregate_id IN "
            "(SELECT prediction_id FROM prediction.predictions WHERE context_id = $1)",
            context_id,
        )
        await pool.execute(
            "DELETE FROM prediction.predictions WHERE context_id = $1", context_id
        )
        await pool.execute(
            "DELETE FROM prediction.context_events WHERE context_id = $1", context_id
        )
        await pool.execute(
            "DELETE FROM prediction.contexts WHERE context_id = $1", context_id
        )


async def _cleanup_downstream_evaluations(
    pool: asyncpg.Pool, prediction_ids: list[uuid.UUID]
) -> None:
    """Remove evaluations the live Verification consumer created from this test's predictions.

    The stack under test is running: a PredictionMade published to RabbitMQ is consumed by
    feed-verification, which independently creates an evaluation + PriceRequested. Deleting only
    the prediction rows would leave orphaned PENDING evaluations behind, and Verification would
    later score a prediction that no longer exists -- feeding a fabricated signal into
    Credibility's learning. Call this for every prediction the test caused to be published.
    """
    if not prediction_ids:
        return
    await pool.execute(
        "DELETE FROM verification.price_observations WHERE request_id IN "
        "(SELECT request_id FROM verification.evaluations WHERE prediction_id = ANY($1::uuid[]))",
        prediction_ids,
    )
    for table in ("scores", "outbox_events", "evaluations"):
        column = "aggregate_id" if table == "outbox_events" else "prediction_id"
        await pool.execute(
            f"DELETE FROM verification.{table} WHERE {column} = ANY($1::uuid[])",  # noqa: S608
            prediction_ids,
        )


@pytest.mark.skipif(
    DATABASE_URL is None or NEO4J_PASSWORD is None,
    reason="DATABASE_URL / NEO4J_PASSWORD not set",
)
async def test_assign_and_close_produces_graph_only_prediction() -> None:
    assert DATABASE_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()
    try:
        await apply_schema(pool)
        settings = PredictionSettings()
        pipeline = PredictionPipeline(
            PredictionRepository(pool), graph, _StubPriceReader(), settings
        )

        parked = await _isolate_stance(pool, AssetId.NEM_NYSE)

        # SAFE_HAVEN_ONLY is required: every seeded MILITARY_CONFLICT edge is conditioned, so a
        # tagless event has nothing to fire (see _event's docstring).
        event = _event(
            AssetId.NEM_NYSE,
            EventType.MILITARY_CONFLICT,
            [ConditionCode.SAFE_HAVEN_ONLY],
        )
        await pipeline.process_event(event)

        window_start, _ = window_bounds(event.first_seen_at, settings.context_window_minutes)
        # ORDER BY context_version DESC picks the context THIS test just created. Against a live
        # database the same (asset, window_start) can already hold older versions from real traffic;
        # an unordered SELECT could return one of those, which is already PREDICTED and therefore not
        # claimable, so the test would back-date a stale row and see nothing produced.
        context_id = await pool.fetchval(
            "SELECT context_id FROM prediction.contexts "
            "WHERE asset_id = $1 AND window_start = $2 "
            "ORDER BY context_version DESC LIMIT 1",
            AssetId.NEM_NYSE.value,
            window_start,
        )
        assert context_id is not None
        # Force the window closed regardless of wall clock, and ensure the state is claimable:
        # claim_ready_contexts only takes OPEN/READY rows.
        await pool.execute(
            "UPDATE prediction.contexts SET state = 'OPEN', "
            "window_end = now() - interval '10 minutes' WHERE context_id = $1",
            context_id,
        )

        produced = await pipeline.close_ready_contexts()
        assert produced >= 1

        # Scope to the asset under test: since E10 this context can also hold *propagated*
        # predictions for OTHER assets (the seeded CORRELATES_WITH edge NEM_NYSE -[UPSTREAM_UP]->
        # XOM_NYSE fires here), and they share this context_id. An unscoped fetchrow could
        # return the propagated XOM_NYSE DOWN row instead of the direct NEM_NYSE UP one.
        row = await pool.fetchrow(
            "SELECT direction, decision_method, status FROM prediction.predictions "
            "WHERE context_id = $1 AND asset_id = $2",
            context_id,
            AssetId.NEM_NYSE.value,
        )
        assert row is not None
        assert row["decision_method"] == "GRAPH_ONLY"
        assert row["direction"] == "UP"
        assert row["status"] == "PENDING"

        # Exactly one outbox event for the DIRECT prediction. Propagated predictions from this
        # same context are counted separately (see the propagation test below).
        outbox_count = await pool.fetchval(
            "SELECT count(*) FROM prediction.outbox_events o "
            "JOIN prediction.predictions p ON p.prediction_id = o.aggregate_id "
            "WHERE p.context_id = $1 AND p.asset_id = $2",
            context_id,
            AssetId.NEM_NYSE.value,
        )
        assert outbox_count == 1

        touched_ids = [
            r["prediction_id"]
            for r in await pool.fetch(
                "SELECT prediction_id FROM prediction.predictions WHERE context_id = $1",
                context_id,
            )
        ]
        await _cleanup(pool, [context_id])
        await _cleanup_downstream_evaluations(pool, touched_ids)
        await _restore_stance(pool, parked)
    finally:
        await graph.close()
        await pool.close()


@pytest.mark.skipif(
    DATABASE_URL is None or RABBITMQ_URL is None or NEO4J_PASSWORD is None,
    reason="DATABASE_URL / RABBITMQ_URL / NEO4J_PASSWORD not set",
)
async def test_outbox_relay_publishes_prediction_made() -> None:
    assert DATABASE_URL is not None and RABBITMQ_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()
    rabbit = RabbitMQClient(RABBITMQ_URL)
    await rabbit.connect()
    try:
        await apply_schema(pool)
        settings = PredictionSettings()
        pipeline = PredictionPipeline(
            PredictionRepository(pool), graph, _StubPriceReader(), settings
        )

        parked = await _isolate_stance(pool, AssetId.XOM_NYSE)

        event = _event(AssetId.XOM_NYSE, EventType.SUPPLY_DISRUPTION)
        await pipeline.process_event(event)
        window_start, _ = window_bounds(event.first_seen_at, settings.context_window_minutes)
        # Newest version, and force it claimable — see the note in the test above.
        context_id = await pool.fetchval(
            "SELECT context_id FROM prediction.contexts "
            "WHERE asset_id = $1 AND window_start = $2 "
            "ORDER BY context_version DESC LIMIT 1",
            AssetId.XOM_NYSE.value,
            window_start,
        )
        assert context_id is not None
        await pool.execute(
            "UPDATE prediction.contexts SET state = 'OPEN', "
            "window_end = now() - interval '10 minutes' WHERE context_id = $1",
            context_id,
        )
        await pipeline.close_ready_contexts()

        publisher = PredictionOutboxPublisher(pool, rabbit)
        delivered = await publisher.publish_pending()
        assert delivered >= 1

        touched_ids = [
            r["prediction_id"]
            for r in await pool.fetch(
                "SELECT prediction_id FROM prediction.predictions WHERE context_id = $1",
                context_id,
            )
        ]
        await _cleanup(pool, [context_id])
        await _cleanup_downstream_evaluations(pool, touched_ids)
        await _restore_stance(pool, parked)
    finally:
        await rabbit.close()
        await graph.close()
        await pool.close()


# --- E10: cross-asset propagation (XOM UP → NEM DOWN) ---


@pytest.mark.skipif(
    DATABASE_URL is None or NEO4J_PASSWORD is None,
    reason="DATABASE_URL / NEO4J_PASSWORD not set",
)
async def test_propagation_produces_downstream_prediction_for_nem() -> None:
    """E10 acceptance criterion 1: MILITARY_CONFLICT/TRANSPORT_AFFECTED on XOM_NYSE produces
    XOM_NYSE UP (direct) AND NEM_NYSE DOWN (propagated via the seeded CORRELATES_WITH edge).

    Requires 08-seed-correlation-edges.cypher applied to the Neo4j container.
    """
    assert DATABASE_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()
    try:
        await apply_schema(pool)
        settings = PredictionSettings()
        pipeline = PredictionPipeline(
            PredictionRepository(pool), graph, _StubPriceReader(), settings
        )

        parked_xom = await _isolate_stance(pool, AssetId.XOM_NYSE)
        parked_nem = await _isolate_stance(pool, AssetId.NEM_NYSE)

        # TRANSPORT_AFFECTED is the condition on the seeded XOM_NYSE CAUSES edge (oil proxy).
        # When XOM_NYSE is predicted UP the seeded CORRELATES_WITH edge fires for NEM_NYSE DOWN.
        event = _event(
            AssetId.XOM_NYSE,
            EventType.MILITARY_CONFLICT,
            [ConditionCode.TRANSPORT_AFFECTED],
        )
        await pipeline.process_event(event)

        window_start, _ = window_bounds(event.first_seen_at, settings.context_window_minutes)
        context_id = await pool.fetchval(
            "SELECT context_id FROM prediction.contexts "
            "WHERE asset_id = $1 AND window_start = $2 "
            "ORDER BY context_version DESC LIMIT 1",
            AssetId.XOM_NYSE.value,
            window_start,
        )
        assert context_id is not None
        await pool.execute(
            "UPDATE prediction.contexts SET state = 'OPEN', "
            "window_end = now() - interval '10 minutes' WHERE context_id = $1",
            context_id,
        )

        produced = await pipeline.close_ready_contexts()
        # At least 2: one direct (XOM) + one propagated (NEM).
        assert produced >= 2, (
            f"Expected at least 2 predictions (direct + propagated), got {produced}. "
            "Check that 08-seed-correlation-edges.cypher has been applied."
        )

        # Verify the direct XOM prediction.
        xom_row = await pool.fetchrow(
            "SELECT direction, payload FROM prediction.outbox_events o "
            "JOIN prediction.predictions p ON p.prediction_id = o.aggregate_id "
            "WHERE p.context_id = $1 AND p.asset_id = $2",
            context_id,
            AssetId.XOM_NYSE.value,
        )
        assert xom_row is not None
        assert xom_row["direction"] == "UP"

        # Verify the propagated NEM prediction: direction DOWN, propagation_depth ≥ 1,
        # non-empty propagation_chain in the outbox payload.
        import json as _json

        nem_rows = await pool.fetch(
            "SELECT p.direction, o.payload FROM prediction.outbox_events o "
            "JOIN prediction.predictions p ON p.prediction_id = o.aggregate_id "
            "WHERE p.asset_id = $1 "
            "ORDER BY p.decision_at DESC LIMIT 5",
            AssetId.NEM_NYSE.value,
        )
        # Find the row produced in this test run (propagation_depth > 0).
        prop_row = next(
            (
                r
                for r in nem_rows
                if _json.loads(r["payload"]).get("propagation_depth", 0) > 0
            ),
            None,
        )
        assert prop_row is not None, "No propagated NEM_NYSE prediction found in outbox"
        assert prop_row["direction"] == "DOWN"
        payload = _json.loads(prop_row["payload"])
        assert payload["propagation_depth"] == 1
        assert len(payload["propagation_chain"]) == 1
        hop = payload["propagation_chain"][0]
        assert hop["source_asset_id"] == "XOM_NYSE"
        assert hop["target_asset_id"] == "NEM_NYSE"
        assert hop["condition"] == "UPSTREAM_UP"

        # Depth 2 convergence: NEM_NYSE DOWN and LUG_STO DOWN both reach SWED_A_STO in the same
        # level, so their forces sum. The seeded NEM edge (UP, 0.40) outweighs the LUG edge
        # (DOWN, 0.15), so the net must be UP -- and BOTH edges must appear as contributors.
        swed_rows = await pool.fetch(
            "SELECT p.direction, o.payload FROM prediction.outbox_events o "
            "JOIN prediction.predictions p ON p.prediction_id = o.aggregate_id "
            "WHERE p.asset_id = $1 AND p.context_id = $2",
            AssetId.SWED_A_STO.value,
            context_id,
        )
        assert len(swed_rows) == 1, (
            "expected exactly one converged SWED_A_STO prediction; "
            "has 08-seed-correlation-edges.cypher been re-applied?"
        )
        swed_payload = _json.loads(swed_rows[0]["payload"])
        assert swed_rows[0]["direction"] == "UP", "net of +0.40 and -0.15 must be UP"
        assert swed_payload["propagation_depth"] == 2
        contributors = {e["edge_id"] for e in swed_payload["contributing_edges"]}
        assert contributors == {
            "NEM_NYSE|UPSTREAM_DOWN->SWED_A_STO",
            "LUG_STO|UPSTREAM_DOWN->SWED_A_STO",
        }, f"both converging edges must be reported, got {contributors}"

        # Capture the ids BEFORE deleting the predictions: Verification may already have created
        # evaluations from the published messages, and those are keyed by prediction_id.
        touched_ids = [
            r["prediction_id"]
            for r in await pool.fetch(
                "SELECT prediction_id FROM prediction.predictions WHERE context_id = $1",
                context_id,
            )
        ]
        # Propagated predictions reuse the SOURCE context_id (_ProxyRecord only re-targets
        # asset_id), so deleting this one context removes the direct prediction and every
        # propagated one -- for all downstream assets, not just NEM_NYSE.
        await _cleanup(pool, [context_id])
        await _cleanup_downstream_evaluations(pool, touched_ids)
        await _restore_stance(pool, parked_xom)
        await _restore_stance(pool, parked_nem)
    finally:
        await graph.close()
        await pool.close()
