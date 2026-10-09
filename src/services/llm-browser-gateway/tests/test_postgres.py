"""Opt-in persistence tests, restricted to a dedicated disposable database."""

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import pytest
from dotenv import dotenv_values

from llm_browser_gateway.adapters.common.types import Result, Status, failure
from llm_browser_gateway.config import ROOT
from llm_browser_gateway.db import PostgresStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("BROWSER_GATEWAY_POSTGRES_TEST") != "1",
        reason="Set BROWSER_GATEWAY_POSTGRES_TEST=1 for disposable DB tests",
    ),
]


async def test_persistent_cooldown_restart_and_uncertain_reservation() -> None:
    import asyncpg

    source = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / "infra" / ".env").get(
        "DATABASE_URL"
    )
    assert source, "Host DATABASE_URL must be configured in infra/.env"
    parsed = urlsplit(source)
    assert parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    url = urlunsplit(parsed._replace(path="/llm_browser_gateway_test"))
    cleanup = await asyncpg.connect(url)
    await cleanup.execute("DROP SCHEMA IF EXISTS llm_browser_gateway CASCADE")
    await cleanup.close()
    store = PostgresStore(url, 30, 7)
    await store.start()
    try:
        assert await store.reserve("test", "chatgpt", "limit", "req")
        snapshot = await store.snapshot("test")
        assert snapshot["active"] == [
            {
                "provider": "chatgpt",
                "slot": 0,
                "request_id": "req",
                "attempt_id": "limit",
                "state": "reserved",
            }
        ]
        assert not await store.reserve("test", "chatgpt", "overlap", "req")
        assert await store.reserve("test", "chatgpt", "in_flight", "req", 1)
        assert not await store.reserve("test", "chatgpt", "same_slot", "req", 1)
        reset = datetime.now(UTC) + timedelta(hours=1)
        error = failure(Status.RATE_LIMITED, submitted=True)
        limited = Result(error.body, 429, error.status, True, True, reset)
        await store.finish("test", "chatgpt", "limit", limited)
        await store.finish("test", "chatgpt", "in_flight", Result({}, 200, Status.SUCCESS, True))
        remaining_limit = await store.availability("test", "chatgpt")
        assert remaining_limit and remaining_limit.reason == Status.RATE_LIMITED
        assert not await store.reserve("test", "chatgpt", "after_limit", "req", 1)
        assert await store.reserve("test", "claude", "interrupted", "req")
        await store.submitted("interrupted")
    finally:
        await store.close()
    restarted = PostgresStore(url, 30, 7)
    await restarted.start()
    try:
        availability = await restarted.availability("test", "chatgpt")
        assert availability and availability.reset_at == reset and not availability.eligible
        unknown = await restarted.availability("test", "claude")
        assert unknown and unknown.reason == "submission_unknown" and unknown.next_check_at is None
        assert not await restarted.reserve("test", "claude", "duplicate", "req")
        assert not await restarted.reserve("test", "claude", "unknown_other_slot", "req", 1)
        await restarted.terminal("test", "claude", "interrupted")
        assert await restarted.reserve("test", "claude", "after_reconciliation", "req")
        await restarted.finish(
            "test", "claude", "after_reconciliation", failure(Status.DISCONNECTED)
        )
        snapshot = await restarted.snapshot("test")
        assert not snapshot["active"]
        assert snapshot["availability"]
    finally:
        await restarted.close()
