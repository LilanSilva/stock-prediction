from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from shared.messaging.client import RabbitMQClient
from shared.schemas.messages import ArticleIngested

from cleansing.config import CleansingSettings
from cleansing.db import apply_schema, create_pool
from cleansing.embedding import HashingEmbedder
from cleansing.extraction import KeywordExtractor, SpacyExtractor
from cleansing.outbox import EventOutboxPublisher
from cleansing.pipeline import CleansingPipeline
from cleansing.repository import CleansingRepository

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL")


def _message() -> ArticleIngested:
    return ArticleIngested(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        article_id=uuid.uuid4(),
        source_id="itest",
        canonical_url=f"https://example.com/{uuid.uuid4()}",
        title="EU imposes fresh sanctions on the exporter",
        body="The European Union announced fresh sanctions targeting oil exports today.",
        published_at=datetime.now(UTC),
        language="en",
        country="US",
        content_hash=uuid.uuid4().hex,
    )


async def _purge_article(pool: asyncpg.Pool, article_id: uuid.UUID) -> None:
    """Remove every row this article produced, whatever state the test reached.

    Must run in a ``finally``: the fingerprint is the dedup guard, so a run that fails before its
    inline cleanup leaves one behind and *every later run* is then discarded as a near-duplicate of
    itself — the test passes once and fails forever after.
    """
    clusters = [
        r["cluster_id"]
        for r in await pool.fetch(
            "SELECT cluster_id FROM cleansing.cluster_articles WHERE article_id = $1", article_id
        )
    ]
    for cluster_id in clusters:
        await pool.execute(
            "DELETE FROM cleansing.outbox_events WHERE aggregate_id IN "
            "(SELECT event_id FROM cleansing.events WHERE cluster_id = $1)",
            cluster_id,
        )
        await pool.execute("DELETE FROM cleansing.events WHERE cluster_id = $1", cluster_id)
        await pool.execute(
            "DELETE FROM cleansing.cluster_articles WHERE cluster_id = $1", cluster_id
        )
        await pool.execute(
            "DELETE FROM cleansing.event_clusters WHERE cluster_id = $1", cluster_id
        )
    await pool.execute(
        "DELETE FROM cleansing.article_actions WHERE article_id = $1", article_id
    )
    await pool.execute(
        "DELETE FROM cleansing.article_embeddings WHERE article_id = $1", article_id
    )
    await pool.execute(
        "DELETE FROM cleansing.article_fingerprints WHERE article_id = $1", article_id
    )


def _swedish_message() -> ArticleIngested:
    return ArticleIngested(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        article_id=uuid.uuid4(),
        source_id="itest-sv",
        canonical_url=f"https://example.com/{uuid.uuid4()}",
        title="Sveriges statsminister avgår efter en misstroendeomröstning i riksdagen",
        body=(
            "Sveriges statsminister meddelade sin avgång efter att riksdagen röstat igenom "
            "en misstroendeförklaring under torsdagen."
        ),
        published_at=datetime.now(UTC),
        language="sv",
        country="SE",
        content_hash=uuid.uuid4().hex,
    )


@pytest.mark.skipif(DATABASE_URL is None, reason="DATABASE_URL not set")
async def test_process_and_close_produces_event_and_outbox() -> None:
    assert DATABASE_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        await apply_schema(pool)
        repo = CleansingRepository(pool)
        settings = CleansingSettings()
        pipeline = CleansingPipeline(repo, HashingEmbedder(1024), KeywordExtractor(), settings)

        message = _message()
        await pipeline.process_article(message)

        # The article created exactly one cluster; force its quiet deadline into the past.
        cluster_id = await pool.fetchval(
            """
            SELECT ca.cluster_id
            FROM cleansing.cluster_articles ca
            WHERE ca.article_id = $1
            """,
            message.article_id,
        )
        assert cluster_id is not None
        await pool.execute(
            "UPDATE cleansing.event_clusters SET quiet_deadline = now() - interval '1 minute' "
            "WHERE cluster_id = $1",
            cluster_id,
        )

        produced = await pipeline.close_ready_clusters()
        assert produced >= 1

        event_row = await pool.fetchrow(
            "SELECT extraction_method FROM cleansing.events WHERE cluster_id = $1",
            cluster_id,
        )
        assert event_row is not None
        assert event_row["extraction_method"] == "LOCAL"

        outbox_count = await pool.fetchval(
            """
            SELECT count(*) FROM cleansing.outbox_events o
            JOIN cleansing.events e ON e.event_id = o.aggregate_id
            WHERE e.cluster_id = $1
            """,
            cluster_id,
        )
        assert outbox_count == 1

    finally:
        # Always: a failure before this point would otherwise leave the dedup fingerprint behind.
        await _purge_article(pool, message.article_id)
        await pool.close()


@pytest.mark.skipif(
    DATABASE_URL is None or RABBITMQ_URL is None, reason="DATABASE_URL/RABBITMQ_URL not set"
)
async def test_outbox_relay_publishes_event_detected() -> None:
    assert DATABASE_URL is not None and RABBITMQ_URL is not None
    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    rabbit = RabbitMQClient(RABBITMQ_URL)
    await rabbit.connect()
    try:
        await apply_schema(pool)
        repo = CleansingRepository(pool)
        settings = CleansingSettings()
        pipeline = CleansingPipeline(repo, HashingEmbedder(1024), KeywordExtractor(), settings)

        message = _message()
        await pipeline.process_article(message)
        cluster_id = await pool.fetchval(
            "SELECT cluster_id FROM cleansing.cluster_articles WHERE article_id = $1",
            message.article_id,
        )
        await pool.execute(
            "UPDATE cleansing.event_clusters SET quiet_deadline = now() - interval '1 minute' "
            "WHERE cluster_id = $1",
            cluster_id,
        )
        await pipeline.close_ready_clusters()

        publisher = EventOutboxPublisher(pool, rabbit)
        delivered = await publisher.publish_pending()
        assert delivered >= 1

    finally:
        # Always: a failure before this point would otherwise leave the dedup fingerprint behind.
        await _purge_article(pool, message.article_id)
        await rabbit.close()
        await pool.close()


@pytest.mark.skipif(DATABASE_URL is None, reason="DATABASE_URL not set")
async def test_swedish_article_extracts_actor_and_produces_event() -> None:
    """End-to-end Swedish assertion: real spaCy sv_core_news_sm actor extraction through close.

    Requires the `ml` extra (spaCy + sv_core_news_sm); skipped cleanly where the model is absent so
    the plain unit/integration run stays green without the heavy ML dependency installed.
    """
    assert DATABASE_URL is not None
    extractor = SpacyExtractor()
    try:
        extractor.load()
    except (ImportError, OSError) as exc:  # spaCy or sv_core_news_sm not installed locally
        pytest.skip(f"spaCy Swedish model unavailable: {exc}")

    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    try:
        await apply_schema(pool)
        repo = CleansingRepository(pool)
        settings = CleansingSettings()
        # HashingEmbedder keeps the test fast/deterministic; the Swedish claim is the spaCy path.
        pipeline = CleansingPipeline(repo, HashingEmbedder(1024), extractor, settings)

        message = _swedish_message()
        await pipeline.process_article(message)

        # spaCy must extract a Swedish subject actor (the keyword backend would leave this NULL).
        actor = await pool.fetchval(
            "SELECT actor FROM cleansing.article_actions WHERE article_id = $1",
            message.article_id,
        )
        assert actor is not None and actor.strip() != ""

        cluster_id = await pool.fetchval(
            "SELECT cluster_id FROM cleansing.cluster_articles WHERE article_id = $1",
            message.article_id,
        )
        assert cluster_id is not None
        await pool.execute(
            "UPDATE cleansing.event_clusters SET quiet_deadline = now() - interval '1 minute' "
            "WHERE cluster_id = $1",
            cluster_id,
        )

        produced = await pipeline.close_ready_clusters()
        assert produced >= 1

        event_row = await pool.fetchrow(
            "SELECT extraction_method, canonical_summary FROM cleansing.events "
            "WHERE cluster_id = $1",
            cluster_id,
        )
        assert event_row is not None
        assert event_row["extraction_method"] == "LOCAL"
        assert event_row["canonical_summary"]

    finally:
        # Always: a failure before this point would otherwise leave the dedup fingerprint behind.
        await _purge_article(pool, message.article_id)
        await pool.close()
