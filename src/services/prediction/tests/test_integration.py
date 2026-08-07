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

        row = await pool.fetchrow(
            "SELECT direction, decision_method, status FROM prediction.predictions "
            "WHERE context_id = $1",
            context_id,
        )
        assert row is not None
        assert row["decision_method"] == "GRAPH_ONLY"
        assert row["direction"] == "UP"
        assert row["status"] == "PENDING"

        outbox_count = await pool.fetchval(
            "SELECT count(*) FROM prediction.outbox_events o "
            "JOIN prediction.predictions p ON p.prediction_id = o.aggregate_id "
            "WHERE p.context_id = $1",
            context_id,
        )
        assert outbox_count == 1

        await _cleanup(pool, [context_id])
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

        await _cleanup(pool, [context_id])
        await _restore_stance(pool, parked)
    finally:
        await rabbit.close()
        await graph.close()
        await pool.close()
