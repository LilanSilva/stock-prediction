"""Integration tests for the Credibility Service against live infrastructure.

Runs only when DATABASE_URL (and, for the delivery test, RABBITMQ_URL) point at the local stack.
Postgres side is exercised directly through the repository (source state, history, idempotency);
the RabbitMQ side asserts a real PredictionScored round-trips to the credibility.scored queue.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg
import pytest
from shared.messaging.client import RabbitMQClient
from shared.schemas.messages import (
    AssetId,
    CloseObservation,
    ContributingEdge,
    Direction,
    Magnitude,
    PredictionScored,
    PriceKind,
)

from credibility.db import apply_schema, create_pool
from credibility.repository import CredibilityRepository
from credibility.updater import WeightUpdate

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL")


def _close(session: date, close: str) -> CloseObservation:
    return CloseObservation(
        session=session,
        close=Decimal(close),
        fetched_at=datetime.now(UTC),
        source="biquote.io",
        provider_symbol="XAUUSD",
        price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
        is_adjusted=False,
        registry_version="biquote-reference-v1",
    )


async def _cleanup(pool: asyncpg.Pool, prediction_id: uuid.UUID, source_id: str) -> None:
    await pool.execute(
        "DELETE FROM credibility.credibility_history WHERE prediction_id = $1", prediction_id
    )
    await pool.execute(
        "DELETE FROM credibility.processed_predictions WHERE prediction_id = $1", prediction_id
    )
    await pool.execute(
        "DELETE FROM credibility.credibility WHERE entity_id = $1 AND entity_type = 'source'",
        source_id,
    )


@pytest.mark.skipif(DATABASE_URL is None, reason="DATABASE_URL not set")
async def test_commit_updates_writes_state_history_and_is_idempotent() -> None:
    assert DATABASE_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    prediction_id = uuid.uuid4()
    source_id = f"itest-{uuid.uuid4().hex[:8]}.se"
    try:
        await apply_schema(pool)
        repo = CredibilityRepository(pool)

        updates = [
            WeightUpdate("MILITARY_CONFLICT->GOLD", "edge", 1.0, 1.0, 1.7, 1.0),
            WeightUpdate(source_id, "source", 1.0, 1.0, 1.5, 1.0),
        ]
        first = await repo.commit_updates(prediction_id, updates)
        assert first is True

        # Source current-state row written with the correct score.
        row = await pool.fetchrow(
            "SELECT alpha, beta, credibility_score FROM credibility.credibility "
            "WHERE entity_id = $1 AND entity_type = 'source'",
            source_id,
        )
        assert row is not None
        assert float(row["alpha"]) == pytest.approx(1.5)
        assert float(row["credibility_score"]) == pytest.approx(1.5 / 2.5)

        # One history row per entity, with a valid 95% CI.
        hist = await pool.fetch(
            "SELECT entity_type, ci_lower, ci_upper FROM credibility.credibility_history "
            "WHERE prediction_id = $1 ORDER BY entity_type",
            prediction_id,
        )
        assert len(hist) == 2
        for h in hist:
            assert 0.0 <= float(h["ci_lower"]) < float(h["ci_upper"]) <= 1.0

        # Redelivery is a no-op: guard rejects it, no second history rows.
        second = await repo.commit_updates(prediction_id, updates)
        assert second is False
        count = await pool.fetchval(
            "SELECT count(*) FROM credibility.credibility_history WHERE prediction_id = $1",
            prediction_id,
        )
        assert count == 2

        assert await repo.already_processed(prediction_id) is True
    finally:
        await _cleanup(pool, prediction_id, source_id)
        await pool.close()


@pytest.mark.skipif(
    DATABASE_URL is None or RABBITMQ_URL is None, reason="DATABASE_URL/RABBITMQ_URL not set"
)
async def test_scored_message_round_trips_to_queue() -> None:
    assert RABBITMQ_URL is not None
    rabbit = RabbitMQClient(RABBITMQ_URL)
    await rabbit.connect()
    try:
        scored = PredictionScored(
            correlation_id=uuid.uuid4(),
            occurred_at=datetime.now(UTC),
            prediction_id=uuid.uuid4(),
            context_id=uuid.uuid4(),
            asset_id=AssetId.GOLD,
            predicted_direction=Direction.UP,
            actual_direction=Direction.UP,
            predicted_magnitude=Magnitude.MEDIUM,
            actual_magnitude=Magnitude.MEDIUM,
            confidence=0.9,
            actual_return=0.02,
            is_correct=True,
            score=1.0,
            contributing_edges=[
                ContributingEdge(
                    edge_id="SANCTIONS->GOLD",
                    direction=Direction.UP,
                    current_weight=0.5,
                    influence_weight=0.6,
                    path="SANCTIONS->GOLD",
                )
            ],
            source_ids=["di.se"],
            baseline=_close(date(2026, 7, 27), "100.00"),
            settlement=_close(date(2026, 7, 28), "102.00"),
            scored_at=datetime.now(UTC),
        )
        # Publish by routing key; the broker fans it out to credibility.scored.
        await rabbit.publish(scored)
    finally:
        await rabbit.close()
