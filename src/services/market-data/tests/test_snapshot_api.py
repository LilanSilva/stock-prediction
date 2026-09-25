"""Legacy endpoints are independent of collector health and snapshot evidence."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import market_data.app as module


@pytest.mark.parametrize("collector_status", ["NOT_STARTED", "RUNNING", "ACTION_REQUIRED"])
def test_legacy_api_compatibility_and_snapshot_status(
    monkeypatch: Any, collector_status: Any
) -> None:
    monkeypatch.setattr("shared.schemas.asset_id.is_known_asset", lambda x: x == "TEST_STOCK")
    pool = AsyncMock()
    pool.fetchval.return_value = True
    monkeypatch.setattr(
        module,
        "get_recent_closes",
        AsyncMock(return_value=[(date(2026, 9, 24), Decimal("123.45"))]),
    )
    monkeypatch.setattr(
        "market_data.snapshots.api.status", AsyncMock(return_value={"status": collector_status})
    )
    module.app.state.ctx = SimpleNamespace(
        pool=pool,
        rabbit=SimpleNamespace(is_connected=True),
        intraday=None,
        scheduler=SimpleNamespace(running=True),
        settings=SimpleNamespace(intraday_enabled=False),
        state=SimpleNamespace(
            last_poll_at=None, due_requests=0, last_published=0, consumed_total=0
        ),
    )
    client = TestClient(module.app)
    assert client.get("/prices/recent", params={"asset_id": "TEST_STOCK"}).json() == {
        "asset_id": "TEST_STOCK",
        "closes": [{"session": "2026-09-24", "close": "123.45"}],
    }
    assert client.get("/health").json() == {
        "status": "ok",
        "last_poll_at": None,
        "due_requests": 0,
        "last_published": 0,
        "consumed_total": 0,
        "intraday_enabled": False,
    }
    assert client.get("/ready").status_code == 200
    assert client.get("/snapshots/status").json() == {"status": collector_status}
    invalid: list[dict[str, str | int]] = [
        {"asset_id": "TEST_STOCK", "limit": 1001},
        {"asset_id": "TEST_STOCK", "before": "2026-09-24T10:00:00"},
        {"asset_id": "UNKNOWN"},
    ]
    for params in invalid:
        assert client.get("/snapshots/recent", params=params).status_code == 422
