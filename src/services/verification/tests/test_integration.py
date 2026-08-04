from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg
import pytest
from shared.messaging.client import RabbitMQClient
from shared.reference import REGISTRY_VERSION
from shared.schemas.messages import (
    AssetId,
    CloseObservation,
    ContributingEdge,
    DecisionMethod,
    Direction,
    Horizon,
    Magnitude,
    PredictionMade,
    PriceKind,
    PriceObserved,
)

from verification.config import VerificationSettings
from verification.db import apply_schema, create_pool
from verification.outbox import VerificationOutboxPublisher
from verification.pipeline import VerificationPipeline
from verification.repository import VerificationRepository

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL")
_DECISION_AT = datetime(2026, 7, 27, 22, 46, tzinfo=UTC)


def _prediction() -> PredictionMade:
    return PredictionMade(
        correlation_id=uuid.uuid4(),
        occurred_at=_DECISION_AT,
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        context_version=1,
        event_ids=[uuid.uuid4()],
        asset_id=AssetId.GOLD,
        direction=Direction.UP,
        magnitude=Magnitude.MEDIUM,
        confidence=1.0,
        horizon=Horizon.ONE_TRADING_DAY,
        rationale="itest",
        contributing_edges=[
            ContributingEdge(
                edge_id="SANCTIONS->GOLD",
                direction=Direction.UP,
                current_weight=0.5,
                influence_weight=0.55,
                path="SANCTIONS->GOLD",
            )
        ],
        decision_at=_DECISION_AT,
        decision_method=DecisionMethod.GRAPH_ONLY,
    )


def _close(session: date, close: str) -> CloseObservation:
    return CloseObservation(
        session=session,
        close=Decimal(close),
        fetched_at=datetime.now(UTC),
        source="biquote.io",
        provider_symbol="XAUUSD",
        price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
        is_adjusted=False,
        registry_version=REGISTRY_VERSION,
    )


async def _cleanup(pool: asyncpg.Pool, prediction_id: uuid.UUID, request_id: uuid.UUID) -> None:
    await pool.execute("DELETE FROM verification.scores WHERE prediction_id = $1", prediction_id)
    await pool.execute(
        "DELETE FROM verification.price_observations WHERE request_id = $1", request_id
    )
    await pool.execute(
        "DELETE FROM verification.outbox_events WHERE aggregate_id = $1", prediction_id
    )
    await pool.execute(
        "DELETE FROM verification.evaluations WHERE prediction_id = $1", prediction_id
    )


@pytest.mark.skipif(DATABASE_URL is None, reason="DATABASE_URL not set")
async def test_prediction_creates_evaluation_and_price_request() -> None:
    assert DATABASE_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        await apply_schema(pool)
        pipeline = VerificationPipeline(VerificationRepository(pool), VerificationSettings())
        prediction = _prediction()
        await pipeline.process_prediction(prediction)

        row = await pool.fetchrow(
            "SELECT request_id, baseline_session, settlement_session, status "
            "FROM verification.evaluations WHERE prediction_id = $1",
            prediction.prediction_id,
        )
        assert row is not None
        assert row["status"] == "PENDING"
        assert row["baseline_session"] == date(2026, 7, 27)
        assert row["settlement_session"] == date(2026, 7, 28)

        outbox_count = await pool.fetchval(
            "SELECT count(*) FROM verification.outbox_events "
            "WHERE aggregate_id = $1 AND message_type = 'PriceRequested'",
            prediction.prediction_id,
        )
        assert outbox_count == 1
        await _cleanup(pool, prediction.prediction_id, row["request_id"])
    finally:
        await pool.close()


@pytest.mark.skipif(DATABASE_URL is None, reason="DATABASE_URL not set")
async def test_price_observed_scores_prediction() -> None:
    assert DATABASE_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        await apply_schema(pool)
        pipeline = VerificationPipeline(VerificationRepository(pool), VerificationSettings())
        prediction = _prediction()
        await pipeline.process_prediction(prediction)

        evaluation = await pool.fetchrow(
            "SELECT request_id, baseline_session, settlement_session "
            "FROM verification.evaluations WHERE prediction_id = $1",
            prediction.prediction_id,
        )
        assert evaluation is not None
        request_id = evaluation["request_id"]

        observed = PriceObserved(
            correlation_id=uuid.uuid4(),
            occurred_at=datetime.now(UTC),
            request_id=request_id,
            prediction_id=prediction.prediction_id,
            asset_id=AssetId.GOLD,
            baseline=_close(evaluation["baseline_session"], "100.00"),
            settlement=_close(evaluation["settlement_session"], "103.00"),
        )
        await pipeline.process_price(observed)

        score_row = await pool.fetchrow(
            "SELECT actual_direction, actual_magnitude, is_correct, score, actual_return "
            "FROM verification.scores WHERE prediction_id = $1",
            prediction.prediction_id,
        )
        assert score_row is not None
        assert score_row["actual_direction"] == "UP"
        assert score_row["actual_magnitude"] == "LARGE"
        assert score_row["is_correct"] is True
        assert score_row["score"] == 1.0

        status = await pool.fetchval(
            "SELECT status FROM verification.evaluations WHERE prediction_id = $1",
            prediction.prediction_id,
        )
        assert status == "SCORED"

        scored_outbox = await pool.fetchval(
            "SELECT count(*) FROM verification.outbox_events "
            "WHERE aggregate_id = $1 AND message_type = 'PredictionScored'",
            prediction.prediction_id,
        )
        assert scored_outbox == 1
        await _cleanup(pool, prediction.prediction_id, request_id)
    finally:
        await pool.close()


@pytest.mark.skipif(
    DATABASE_URL is None or RABBITMQ_URL is None, reason="DATABASE_URL/RABBITMQ_URL not set"
)
async def test_outbox_relay_publishes_price_requested() -> None:
    assert DATABASE_URL is not None and RABBITMQ_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    rabbit = RabbitMQClient(RABBITMQ_URL)
    await rabbit.connect()
    try:
        await apply_schema(pool)
        pipeline = VerificationPipeline(VerificationRepository(pool), VerificationSettings())
        prediction = _prediction()
        await pipeline.process_prediction(prediction)

        publisher = VerificationOutboxPublisher(pool, rabbit)
        delivered = await publisher.publish_pending()
        assert delivered >= 1

        request_id = await pool.fetchval(
            "SELECT request_id FROM verification.evaluations WHERE prediction_id = $1",
            prediction.prediction_id,
        )
        await _cleanup(pool, prediction.prediction_id, request_id)
    finally:
        await rabbit.close()
        await pool.close()
