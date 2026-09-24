"""Opt-in migration checks using a uniquely named, disposable database, never the live feed DB."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from cleansing.db import apply_schema
from cleansing.models import ArticleFacts, ExtractedAction
from cleansing.repository import CleansingRepository
from dotenv import dotenv_values
from shared.schemas.messages import EventType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("CLEANSING_DB_TESTS") != "1", reason="CLEANSING_DB_TESTS=1 required"
    ),
]


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    values = dotenv_values(Path(__file__).resolve().parents[4] / "infra/.env")
    admin = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        database="postgres",
        user=values.get("POSTGRES_USER") or "feed_user",
        password=values.get("POSTGRES_PASSWORD"),
    )
    name = "cleansing_quality_test_" + uuid.uuid4().hex
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        created = True
        test_pool = await asyncpg.create_pool(
            host="127.0.0.1",
            port=5432,
            database=name,
            user=values.get("POSTGRES_USER") or "feed_user",
            password=values.get("POSTGRES_PASSWORD"),
            min_size=1,
            max_size=2,
        )
        try:
            await test_pool.execute("CREATE SCHEMA cleansing; CREATE EXTENSION vector")
            yield test_pool
        finally:
            await test_pool.close()
    finally:
        if created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def test_legacy_migration_twice_and_version_filtered_reads(pool: asyncpg.Pool) -> None:
    await pool.execute("""CREATE TABLE cleansing.article_fingerprints (
        article_id UUID PRIMARY KEY, simhash TEXT NOT NULL, source_id TEXT NOT NULL,
        published_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ DEFAULT now())""")
    old_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO cleansing.article_fingerprints VALUES ($1,'123','test',now())", old_id
    )
    await apply_schema(pool)
    await apply_schema(pool)
    now = datetime.now(UTC)
    current = CleansingRepository(pool, processing_version="test-current")
    assert await current.article_already_processed(old_id)
    await current.store_fingerprint(uuid.uuid4(), 456, "test", now)
    assert await current.recent_fingerprints(now - timedelta(days=1)) == [456]
    assert (
        await pool.fetchval(
            "SELECT processing_version FROM cleansing.article_fingerprints WHERE article_id=$1",
            old_id,
        )
        == "legacy"
    )


async def test_cluster_versions_audit_and_embedding_round_trip(pool: asyncpg.Pool) -> None:
    await apply_schema(pool)
    now = datetime.now(UTC)
    facts = ArticleFacts(
        uuid.uuid4(),
        "test",
        "https://example.com/test",
        "Test earnings",
        "Body",
        "en",
        "US",
        now,
        uuid.uuid4(),
    )
    vector = [1.0] + [0.0] * 1023
    old = CleansingRepository(pool, processing_version="old")
    current = CleansingRepository(pool, processing_version="current")
    await old.create_cluster(
        facts, EventType.CORPORATE_EARNINGS, vector, quiet_deadline=now, lifetime_deadline=now
    )
    assert await current.find_candidate_clusters(vector, EventType.CORPORATE_EARNINGS) == []
    assert len(await old.find_candidate_clusters(vector, EventType.CORPORATE_EARNINGS)) == 1
    audit = {"reason": "tiered_rule", "evidence": "Räntan höjs"}
    await current.store_action(
        facts.article_id,
        ExtractedAction(
            None, None, None, None, EventType.CORPORATE_EARNINGS, classification_audit=audit
        ),
        "en",
    )
    await current.store_embedding(facts.article_id, vector)
    saved = await pool.fetchval(
        "SELECT classification_audit FROM cleansing.article_actions WHERE article_id=$1",
        facts.article_id,
    )
    assert json.loads(saved) == audit
    assert (
        await pool.fetchval(
            "SELECT processing_version FROM cleansing.article_embeddings WHERE article_id=$1",
            facts.article_id,
        )
        == "current"
    )
