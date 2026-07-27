"""Integration and application tests against the live local stack (Postgres + RabbitMQ).

Skipped unless DATABASE_URL and RABBITMQ_URL are present. Run with the stack up:
    docker compose --env-file infra/.env -f infra/docker-compose.yml up -d
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from shared.messaging.client import RabbitMQClient

from ingestion.app import app
from ingestion.db import apply_schema, create_pool
from ingestion.models import RawArticle
from ingestion.retention import RetentionCleaner
from ingestion.storage import ArticleRepository, OutboxPublisher

DATABASE_URL = os.environ.get("DATABASE_URL")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL")

pytestmark = pytest.mark.integration

_requires_infra = pytest.mark.skipif(
    not DATABASE_URL or not RABBITMQ_URL,
    reason="DATABASE_URL and RABBITMQ_URL required for live integration tests",
)


@_requires_infra
async def test_store_is_idempotent_and_outbox_delivers() -> None:
    assert DATABASE_URL and RABBITMQ_URL
    pool = await create_pool(DATABASE_URL)
    await apply_schema(pool)
    rabbit = RabbitMQClient(RABBITMQ_URL)
    await rabbit.connect()

    url = f"https://www.di.se/nyheter/it-{uuid.uuid4().hex}"
    raw = RawArticle(
        source_id="di",
        url=url,
        title="Oljepriset stiger",
        summary="Brent stiger.",
        published_at=datetime.now(UTC),
        language="sv",
        country="SE",
    )
    repo = ArticleRepository(pool)
    try:
        first = await repo.store_new_article(
            raw, "En längre brödtext.", correlation_id=uuid.uuid4()
        )
        assert first is not None

        # Same canonical URL -> idempotent no-op, no second message.
        second = await repo.store_new_article(
            raw, "En längre brödtext.", correlation_id=uuid.uuid4()
        )
        assert second is None

        outbox = OutboxPublisher(pool, rabbit)
        published = await outbox.publish_pending()
        assert published >= 1

        status = await pool.fetchval(
            "SELECT delivery_status FROM ingestion.outbox WHERE message_id = $1",
            first.message_id,
        )
        assert status == "DELIVERED"
    finally:
        await pool.execute(
            """
            DELETE FROM ingestion.outbox
            WHERE aggregate_id IN (
                SELECT article_id FROM ingestion.articles WHERE canonical_url = $1
            )
            """,
            url,
        )
        await pool.execute("DELETE FROM ingestion.articles WHERE canonical_url = $1", url)
        await rabbit.close()
        await pool.close()


@_requires_infra
async def test_retention_deletes_aged_rows_only() -> None:
    assert DATABASE_URL
    pool = await create_pool(DATABASE_URL)
    await apply_schema(pool)

    old_url = f"https://www.di.se/old-{uuid.uuid4().hex}"
    new_url = f"https://www.di.se/new-{uuid.uuid4().hex}"
    try:
        for url, age_days in ((old_url, 40), (new_url, 0)):
            await pool.execute(
                """
                INSERT INTO ingestion.articles (
                    article_id, source_id, canonical_url, title, body,
                    published_at, language, country, content_hash, ingested_at
                )
                VALUES ($1, 'di', $2, 't', 'b', now(), 'sv', 'SE', $3,
                        now() - make_interval(days => $4))
                """,
                uuid.uuid4(),
                url,
                uuid.uuid4().hex,
                age_days,
            )

        cleaner = RetentionCleaner(pool, article_retention_days=30, outbox_retention_days=7)
        articles_deleted, _ = await cleaner.run()

        assert articles_deleted >= 1
        remaining_old = await pool.fetchval(
            "SELECT count(*) FROM ingestion.articles WHERE canonical_url = $1", old_url
        )
        remaining_new = await pool.fetchval(
            "SELECT count(*) FROM ingestion.articles WHERE canonical_url = $1", new_url
        )
        assert remaining_old == 0  # aged row deleted
        assert remaining_new == 1  # fresh row kept
    finally:
        await pool.execute(
            "DELETE FROM ingestion.articles WHERE canonical_url = ANY($1::text[])",
            [old_url, new_url],
        )
        await pool.close()


@_requires_infra
def test_health_and_ready_endpoints() -> None:
    # TestClient runs the lifespan: connects Postgres + RabbitMQ and starts the scheduler.
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        ready = client.get("/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["ready"] is True
        assert body["checks"] == {"postgres": True, "rabbitmq": True}
