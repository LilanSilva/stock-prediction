"""The Avanza collector is supervised by the Market Data API process."""

import asyncio
from typing import Any

import pytest

from market_data.app import _run_snapshot_loop
from market_data.snapshots.config import SnapshotSettings


@pytest.mark.asyncio
async def test_snapshot_loop_restarts_after_failure_and_stops_on_shutdown(
    monkeypatch: Any,
) -> None:
    settings = SnapshotSettings(database_url="unused", rabbitmq_url="unused", enabled=True)
    restarted = asyncio.Event()
    calls = 0

    async def fake_run(received: SnapshotSettings, *, configure_logging: bool) -> None:
        nonlocal calls
        assert received is settings
        assert not configure_logging
        calls += 1
        if calls == 1:
            raise RuntimeError("transient failure")
        restarted.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("market_data.snapshots.worker.run", fake_run)
    task = asyncio.create_task(_run_snapshot_loop(settings, restart_delay=0.001))
    await asyncio.wait_for(restarted.wait(), timeout=1)
    assert calls == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
