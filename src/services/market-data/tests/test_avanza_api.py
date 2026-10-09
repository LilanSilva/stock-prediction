from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from market_data.app import app
from market_data.avanza import AvanzaPending, summarize
from tests.test_avanza_primary import ASSET, NOW, OPEN, SERIES, WINDOW, close, sample


def test_recent_prices_preserves_shape_and_exposes_actual_source(monkeypatch: pytest.MonkeyPatch):
    primary = SimpleNamespace(recent=AsyncMock(return_value=[close(OPEN.date(), True)]))
    monkeypatch.setattr(app.state, "ctx", SimpleNamespace(primary_prices=primary), raising=False)
    response = TestClient(app).get("/prices/recent", params={"asset_id": str(ASSET), "sessions": 1})
    assert response.status_code == 200
    value = response.json()["closes"][0]
    assert value["close"] == "20.32" and value["session"] == OPEN.date().isoformat()
    assert value["source"] == "avanza" and value["price_basis"] == "AVANZA_SAMPLED_CLOSE"
    assert value["currency"] == SERIES.currency and value["quote_delay_seconds"] == 900


def test_both_sources_unavailable_is_explicit_without_exception_details(
    monkeypatch: pytest.MonkeyPatch,
):
    primary = SimpleNamespace(recent=AsyncMock(side_effect=RuntimeError("sensitive details")))
    monkeypatch.setattr(app.state, "ctx", SimpleNamespace(primary_prices=primary), raising=False)
    response = TestClient(app).get("/prices/recent", params={"asset_id": str(ASSET)})
    assert response.status_code == 503
    assert response.json() == {"detail": "PRICE_SOURCES_UNAVAILABLE"}


def test_sampled_ohlc_route_returns_coverage_and_original_times(monkeypatch: pytest.MonkeyPatch):
    result = summarize(ASSET, WINDOW, [sample(0), sample(10), sample(20)], NOW)
    monkeypatch.setattr("market_data.snapshots.api._reader",
                        lambda _: SimpleNamespace(summary=AsyncMock(return_value=result)))
    response = TestClient(app).get("/snapshots/ohlc", params={
        "asset_id": str(ASSET), "session": OPEN.date().isoformat(),
    })
    assert response.status_code == 200
    assert response.json()["complete"] and not response.json()["provisional"]
    assert response.json()["last_sample"]["provider_quote_at"] is None


def test_ohlc_opening_pending_is_not_an_error_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("market_data.snapshots.api._reader", lambda _: SimpleNamespace(
        summary=AsyncMock(side_effect=AvanzaPending("OPENING_SAMPLE_NOT_DUE"))))
    response = TestClient(app).get("/snapshots/ohlc", params={
        "asset_id": str(ASSET), "session": OPEN.date().isoformat(),
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "OPENING_SAMPLE_NOT_DUE"
