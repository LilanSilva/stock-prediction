"""Integration and application tests against the live local stack (Postgres + RabbitMQ).

Skipped unless DATABASE_URL and RABBITMQ_URL are present. Run with the stack up:
    docker compose --env-file infra/.env -f infra/docker-compose.yml up -d
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg
import pytest
from fastapi.testclient import TestClient
from shared.reference import REGISTRY_VERSION
from shared.schemas.messages import (
    AssetId,
    CloseObservation,
    PriceKind,
    PriceRequested,
)

from market_data.app import app
from market_data.db import apply_schema, create_pool
from market_data.storage import OutboxPublisher, PriceRequestRepository, build_price_observed

if TYPE_CHECKING:
    from shared.schemas.messages import PriceObserved

DATABASE_URL = os.environ.get("DATABASE_URL")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL")

pytestmark = pytest.mark.integration

_requires_infra = pytest.mark.skipif(
    not DATABASE_URL or not RABBITMQ_URL,
    reason="DATABASE_URL and RABBITMQ_URL required for live integration tests",
)


class _RecordingPublisher:
    """Captures what the outbox would publish instead of sending it to the live exchange.

    Publishing for real here would leak: this test fabricates a `prediction_id`, so the live
    Verification consumer receives a `PriceObserved` with no matching evaluation, rejects it as
    terminal, and leaves a permanent row in `verification.prices.dlq` on every run. The outbox
    logic, the Postgres round-trip and the DELIVERED transition are all still exercised — only the
    broker hop is stubbed. The broker hop itself is covered by
    `test_publish_consume_roundtrip_live` in `src/shared/tests/test_messaging.py`, which round-trips
    through the real exchange on its own auto-delete queue, so no service consumer is affected.
    """

    def __init__(self) -> None:
        self.published: list[PriceObserved] = []

    async def publish(self, message: PriceObserved) -> None:
        self.published.append(message)


def _observation(session: date, close: str) -> CloseObservation:
    return CloseObservation(
        session=session,
        close=Decimal(close),
        provider_bar_time=datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        fetched_at=datetime.now(UTC),
        source="biquote.io",
        provider_symbol="XAUUSD",
        price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
        is_adjusted=False,
        registry_version=REGISTRY_VERSION,
    )


def _price_requested(request_id: uuid.UUID) -> PriceRequested:
    return PriceRequested(
        message_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        request_id=request_id,
        prediction_id=uuid.uuid4(),
        asset_id=AssetId.NEM_NYSE,
        baseline_session=date(2026, 7, 10),
        settlement_session=date(2026, 7, 13),
        market_calendar="COMEX",
    )


async def _cleanup(pool: asyncpg.Pool, request_id: uuid.UUID) -> None:
    await pool.execute("DELETE FROM market_data.outbox WHERE aggregate_id = $1", request_id)
    await pool.execute(
        "DELETE FROM market_data.price_requests WHERE request_id = $1", request_id
    )
    await pool.execute(
        "DELETE FROM market_data.close_observations "
        "WHERE registry_version = $1 AND session = ANY($2::date[])",
        REGISTRY_VERSION,
        [date(2026, 7, 10), date(2026, 7, 13)],
    )


@_requires_infra
async def test_register_is_idempotent_and_outbox_delivers() -> None:
    assert DATABASE_URL and RABBITMQ_URL
    pool = await create_pool(DATABASE_URL)
    await apply_schema(pool)

    request_id = uuid.uuid4()
    repo = PriceRequestRepository(pool)
    try:
        # First delivery registers the request; a duplicate request_id is an idempotent no-op.
        assert await repo.register_request(_price_requested(request_id)) is True
        assert await repo.register_request(_price_requested(request_id)) is False

        # Complete the request with both closes and enqueue the single PriceObserved.
        request = next(r for r in await repo.load_open_requests() if r.request_id == request_id)
        message = build_price_observed(
            request,
            _observation(date(2026, 7, 10), "3315.0"),
            _observation(date(2026, 7, 13), "3290.25"),
        )
        assert await repo.complete_request(message) is True
        # A second completion enqueues no second outbox row (idempotent on request_id).
        assert await repo.complete_request(message) is False

        # The outbox relays the PriceObserved and marks it DELIVERED. The publisher is a spy: this
        # request carries a fabricated prediction_id, so publishing for real would dead-letter in
        # verification.prices.dlq forever (see _RecordingPublisher).
        spy = _RecordingPublisher()
        outbox = OutboxPublisher(pool, spy)
        published = await outbox.publish_pending()
        assert published >= 1

        # The relayed message is the real one built from the persisted request and both closes.
        relayed = next(m for m in spy.published if m.request_id == request_id)
        assert relayed.asset_id == AssetId.NEM_NYSE
        assert relayed.baseline.close == Decimal("3315.0")
        assert relayed.settlement.close == Decimal("3290.25")

        status = await pool.fetchval(
            "SELECT delivery_status FROM market_data.outbox WHERE aggregate_id = $1",
            request_id,
        )
        assert status == "DELIVERED"
    finally:
        await _cleanup(pool, request_id)
        await pool.close()


@_requires_infra
async def test_both_closes_are_persisted_immutably() -> None:
    assert DATABASE_URL
    pool = await create_pool(DATABASE_URL)
    await apply_schema(pool)

    request_id = uuid.uuid4()
    repo = PriceRequestRepository(pool)
    try:
        await repo.register_request(_price_requested(request_id))
        request = next(r for r in await repo.load_open_requests() if r.request_id == request_id)
        message = build_price_observed(
            request,
            _observation(date(2026, 7, 10), "3315.0"),
            _observation(date(2026, 7, 13), "3290.25"),
        )
        await repo.complete_request(message)

        rows = await pool.fetch(
            """
            SELECT session, close, price_kind, is_adjusted, registry_version
            FROM market_data.close_observations
            WHERE registry_version = $1 AND session = ANY($2::date[])
            ORDER BY session
            """,
            REGISTRY_VERSION,
            [date(2026, 7, 10), date(2026, 7, 13)],
        )
        assert len(rows) == 2
        assert all(r["price_kind"] == "PROVIDER_DAILY_CLOSE" for r in rows)
        assert all(r["is_adjusted"] is False for r in rows)
    finally:
        await _cleanup(pool, request_id)
        await pool.close()


@_requires_infra
def test_health_and_ready_endpoints() -> None:
    # TestClient runs the lifespan: connects Postgres + RabbitMQ, starts the scheduler and consumer.
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        ready = client.get("/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["ready"] is True
        assert body["checks"] == {
            "postgres": True,
            "rabbitmq": True,
            "scheduler": True,
            "registry": True,
        }
