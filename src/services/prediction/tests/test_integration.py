from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from shared.graph import CausalGraphClient, Neo4jSettings
from shared.messaging.client import RabbitMQClient
from shared.schemas.messages import AssetId, EventDetected, EventType, ExtractionMethod

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


def _event(asset: AssetId, event_type: EventType) -> EventDetected:
    now = datetime.now(UTC)
    return EventDetected(
        correlation_id=uuid.uuid4(),
        occurred_at=now,
        event_id=uuid.uuid4(),
        cluster_id=uuid.uuid4(),
        canonical_summary="itest event",
        event_type=event_type,
        affected_asset_ids=[asset],
        first_seen_at=now,
        last_seen_at=now,
        extraction_method=ExtractionMethod.LOCAL,
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

        event = _event(AssetId.GOLD, EventType.MILITARY_CONFLICT)
        await pipeline.process_event(event)

        window_start, _ = window_bounds(event.first_seen_at, settings.context_window_minutes)
        context_id = await pool.fetchval(
            "SELECT context_id FROM prediction.contexts WHERE asset_id = $1 AND window_start = $2",
            AssetId.GOLD.value,
            window_start,
        )
        assert context_id is not None
        # Force the window closed regardless of wall clock.
        await pool.execute(
            "UPDATE prediction.contexts SET window_end = now() - interval '10 minutes' "
            "WHERE context_id = $1",
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

        event = _event(AssetId.BRENT_OIL, EventType.SUPPLY_DISRUPTION)
        await pipeline.process_event(event)
        window_start, _ = window_bounds(event.first_seen_at, settings.context_window_minutes)
        context_id = await pool.fetchval(
            "SELECT context_id FROM prediction.contexts WHERE asset_id = $1 AND window_start = $2",
            AssetId.BRENT_OIL.value,
            window_start,
        )
        await pool.execute(
            "UPDATE prediction.contexts SET window_end = now() - interval '10 minutes' "
            "WHERE context_id = $1",
            context_id,
        )
        await pipeline.close_ready_contexts()

        publisher = PredictionOutboxPublisher(pool, rabbit)
        delivered = await publisher.publish_pending()
        assert delivered >= 1

        await _cleanup(pool, [context_id])
    finally:
        await rabbit.close()
        await graph.close()
        await pool.close()
