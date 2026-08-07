"""Unit tests for the recent-closes read query bounds and the /prices/recent endpoint.

These need no live database: the query is exercised against a fake pool that records the bound
LIMIT, and the endpoint's `get_recent_closes` dependency is monkeypatched. Request-validation
cases (unknown asset id, out-of-range sessions) reject before the handler body runs, so they need
no app context.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from shared.schemas.messages import AssetId

import market_data.app as app_module
from market_data.app import app
from market_data.storage import (
    MAX_RECENT_SESSIONS,
    MIN_RECENT_SESSIONS,
    get_recent_closes,
)


class _FakePool:
    """Minimal asyncpg.Pool stand-in that captures the LIMIT parameter and returns fixed rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_asset_id: str | None = None
        self.last_limit: int | None = None

    async def fetch(self, _query: str, *args: Any) -> list[dict[str, Any]]:
        self.last_asset_id, self.last_limit = args[0], args[1]
        return self._rows


async def test_get_recent_closes_clamps_above_max() -> None:
    pool = _FakePool([])
    await get_recent_closes(pool, AssetId.NEM_NYSE, 10_000)  # type: ignore[arg-type]
    assert pool.last_limit == MAX_RECENT_SESSIONS
    assert pool.last_asset_id == "NEM_NYSE"


async def test_get_recent_closes_clamps_below_min() -> None:
    pool = _FakePool([])
    await get_recent_closes(pool, AssetId.XOM_NYSE, 0)  # type: ignore[arg-type]
    assert pool.last_limit == MIN_RECENT_SESSIONS


async def test_get_recent_closes_returns_session_close_pairs() -> None:
    pool = _FakePool(
        [
            {"session": date(2026, 7, 13), "close": Decimal("3290.25")},
            {"session": date(2026, 7, 10), "close": Decimal("3315.0")},
        ]
    )
    pairs = await get_recent_closes(pool, AssetId.NEM_NYSE, 5)  # type: ignore[arg-type]
    assert pairs == [
        (date(2026, 7, 13), Decimal("3290.25")),
        (date(2026, 7, 10), Decimal("3315.0")),
    ]


def test_prices_recent_serializes_closes_as_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_recent_closes(
        _pool: object, _asset_id: AssetId, _sessions: int
    ) -> list[tuple[date, Decimal]]:
        return [
            (date(2026, 7, 13), Decimal("3290.25")),
            (date(2026, 7, 10), Decimal("3315.0")),
        ]

    monkeypatch.setattr(app_module, "get_recent_closes", fake_get_recent_closes)
    app.state.ctx = SimpleNamespace(pool=object())

    client = TestClient(app)
    resp = client.get("/prices/recent", params={"asset_id": "NEM_NYSE", "sessions": 5})

    assert resp.status_code == 200
    assert resp.json() == {
        "asset_id": "NEM_NYSE",
        "closes": [
            {"session": "2026-07-13", "close": "3290.25"},
            {"session": "2026-07-10", "close": "3315.0"},
        ],
    }


def test_prices_recent_defaults_to_twenty_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, int] = {}

    async def fake_get_recent_closes(
        _pool: object, _asset_id: AssetId, sessions: int
    ) -> list[tuple[date, Decimal]]:
        captured["sessions"] = sessions
        return []

    monkeypatch.setattr(app_module, "get_recent_closes", fake_get_recent_closes)
    app.state.ctx = SimpleNamespace(pool=object())

    client = TestClient(app)
    resp = client.get("/prices/recent", params={"asset_id": "XOM_NYSE"})

    assert resp.status_code == 200
    assert captured["sessions"] == 20


def test_prices_recent_rejects_unknown_asset() -> None:
    client = TestClient(app)
    resp = client.get("/prices/recent", params={"asset_id": "SILVER", "sessions": 5})
    assert resp.status_code == 422


@pytest.mark.parametrize("sessions", [0, MAX_RECENT_SESSIONS + 1])
def test_prices_recent_rejects_out_of_range_sessions(sessions: int) -> None:
    client = TestClient(app)
    resp = client.get("/prices/recent", params={"asset_id": "NEM_NYSE", "sessions": sessions})
    assert resp.status_code == 422
