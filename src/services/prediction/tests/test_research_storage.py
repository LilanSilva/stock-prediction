"""Opt-in PostgreSQL tests restricted to a dedicated database named e15_test."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio

from prediction.db import apply_schema
from prediction.research import ResearchRepository, build_opportunity, content_hash
from prediction.research_export import export_evidence

from .test_pipeline import _context, _event
from .test_research import context_event

DSN = os.environ.get("E15_TEST_DATABASE_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DSN, reason="isolated DB required")]


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    assert DSN is not None
    if urlparse(DSN).path != "/e15_test":
        pytest.fail("Refusing to run research integration tests outside database e15_test")
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
    try:
        assert await pool.fetchval("SELECT current_database()") == "e15_test"
        await apply_schema(pool)
        await apply_schema(pool)  # Startup migration must be repeatable.
        await pool.execute(
            "TRUNCATE prediction.research_predictions, prediction.research_opportunities, "
            "prediction.research_event_versions",
        )
        yield pool
    finally:
        await pool.close()


async def test_first_receipt_survives_redelivery_and_revisions_are_distinct(
    pool: asyncpg.Pool,
) -> None:
    repo = ResearchRepository(pool)
    event = _event()
    await repo.record_event(event)
    versions = await repo.load_events([event.event_id], datetime.now(UTC))
    assert len(versions) == 1
    assert await repo.load_events([event.event_id], event.first_seen_at) == []
    await repo.record_event(event.model_copy(update={"message_id": uuid.uuid4()}))
    assert await repo.load_events([event.event_id], datetime.now(UTC)) == versions
    await repo.record_event(event.model_copy(update={"canonical_summary": "a correction"}))
    versions = await repo.load_events([event.event_id], datetime.now(UTC))
    assert len(versions) == 2
    result = build_opportunity(_context(), [context_event(event)], versions, [], False, None,
                               datetime.now(UTC), {})
    assert result.quality == "INVALID_INPUT"


async def test_atomic_capture_and_concurrent_redelivery_never_touch_outbox(
    pool: asyncpg.Pool,
) -> None:
    repo = ResearchRepository(pool)
    event = _event()
    await repo.record_event(event)
    cutoff = datetime.now(UTC)
    result = build_opportunity(
        _context(), [context_event(event)], await repo.load_events([event.event_id], cutoff),
        [], False, None, cutoff, {},
    )
    assert sorted(await asyncio.gather(repo.save(result), repo.save(result))) == [False, True]
    assert await pool.fetchval("SELECT count(*) FROM prediction.research_opportunities") == 1
    assert await pool.fetchval("SELECT count(*) FROM prediction.research_predictions") == 1
    available_at = await pool.fetchval("SELECT available_at FROM prediction.research_predictions")
    assert available_at is not None
    assert await pool.fetchval("SELECT count(*) FROM prediction.outbox_events") == 0
    original = await pool.fetchval("SELECT snapshot FROM prediction.research_opportunities")
    assert not await repo.save(replace(result, snapshot="modified"))
    assert await pool.fetchval("SELECT snapshot FROM prediction.research_opportunities") == original
    assert await pool.fetchval(
        "SELECT available_at FROM prediction.research_predictions",
    ) == available_at

    # Simulate a process dying after the atomic pair commit but before availability recording.
    await pool.execute("UPDATE prediction.research_predictions SET available_at = NULL")
    assert not await repo.save(result)
    assert await pool.fetchval(
        "SELECT available_at FROM prediction.research_predictions",
    ) >= available_at

    # If the second insert fails, the first insert must roll back, not leave a partial pair.
    broken = replace(result, opportunity_id=uuid.uuid4(), context_id=uuid.uuid4())
    broken = replace(broken, result_status="invalid")  # type: ignore[arg-type]
    with pytest.raises(asyncpg.CheckViolationError):
        await repo.save(broken)
    assert await pool.fetchval("SELECT count(*) FROM prediction.research_opportunities") == 1


async def test_export_is_reproducible_and_filters_by_durable_receipt(
    pool: asyncpg.Pool, tmp_path: Path,
) -> None:
    repo = ResearchRepository(pool)
    event = _event()
    await repo.record_event(event)
    cutoff = datetime.now(UTC)
    result = build_opportunity(
        _context(), [context_event(event)], await repo.load_events([event.event_id], cutoff),
        [], False, None, cutoff, {},
    )
    await repo.save(result)
    old = await export_evidence(pool, tmp_path / "before", cutoff)
    assert old["rows"] == 0  # Input time alone cannot imply the result was already available.
    cutoff = datetime.now(UTC)
    one = await export_evidence(pool, tmp_path / "one", cutoff)
    two = await export_evidence(pool, tmp_path / "two", cutoff)
    assert one == two
    assert one["rows"] == 1
    assert one["training_eligible"] is False
    assert one["sha256"] == content_hash((tmp_path / "one/opportunities.jsonl").read_text())
    assert json.loads((tmp_path / "one/manifest.json").read_text()) == one
    assert one["kg_status_counts"] == {"ABSTAINED": 1}
    with pytest.raises(FileExistsError):
        await export_evidence(pool, tmp_path / "one", cutoff)
    with pytest.raises(ValueError, match="future"):
        await export_evidence(pool, tmp_path / "future", cutoff + timedelta(days=1))
    assert not (tmp_path / "future").exists()


async def test_corrupt_snapshot_cannot_produce_complete_export(
    pool: asyncpg.Pool, tmp_path: Path,
) -> None:
    repo = ResearchRepository(pool)
    result = build_opportunity(_context(), [], [], [], False, None, datetime.now(UTC), {})
    await repo.save(replace(result, snapshot_hash="corrupt"))
    with pytest.raises(ValueError, match="integrity"):
        await export_evidence(pool, tmp_path / "broken", datetime.now(UTC))
    assert not (tmp_path / "broken/manifest.json").exists()
