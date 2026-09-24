import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from verification.app import app
from verification.config import VerificationSettings


async def test_shadow_report_found_missing_and_invalid_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app.state,
        "ctx",
        SimpleNamespace(pool=AsyncMock(), settings=VerificationSettings()),
        raising=False,
    )
    report = AsyncMock(return_value={"mode": "SHADOW", "status": "UNSCORABLE"})
    monkeypatch.setattr("verification.app.IntradayVerification.report", report)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/verification/intraday/{uuid.uuid4()}")
        assert response.status_code == 200 and response.json()["status"] == "UNSCORABLE"
        report.return_value = None
        assert (await client.get(f"/verification/intraday/{uuid.uuid4()}")).status_code == 404
        assert (await client.get("/verification/intraday/not-a-uuid")).status_code == 422


async def test_summary_keeps_status_cohorts_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app.state,
        "ctx",
        SimpleNamespace(pool=AsyncMock(), settings=VerificationSettings()),
        raising=False,
    )
    monkeypatch.setattr(
        "verification.app.IntradayVerification.summary",
        AsyncMock(
            return_value=[
                {"policy_hash": "test", "status": "SCORED", "evaluations": 2, "hits": 1},
                {"policy_hash": "test", "status": "UNSCORABLE", "evaluations": 3, "hits": 1},
            ]
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/verification/intraday")
    assert response.status_code == 200
    assert response.json()["mode"] == "OFF"
    assert len(response.json()["cohorts"]) == 2
