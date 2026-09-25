"""Shadow evidence replay/finalization against opt-in disposable PostgreSQL."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from shared.schemas.messages import PredictionMade, PriceSampleObserved

from verification.config import VerificationSettings
from verification.intraday_policy import SessionWindow
from verification.sampled import DDL, SampleVerification
from verification.sampled_policy import SampleResult

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_duplicate_samples_finalization_and_withdrawal(monkeypatch: Any) -> None:
    url = os.environ.get("SNAPSHOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SNAPSHOT_TEST_DATABASE_URL not configured")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    assert await pool.fetchval("SELECT current_database()") == "snapshot_test"
    try:
        await pool.execute("CREATE SCHEMA IF NOT EXISTS verification")
        await pool.execute(DDL)
        await pool.execute(DDL)
        await pool.execute(
            "TRUNCATE verification.sample_evidence,verification.sample_evaluations,"
            "verification.sample_withdrawals"
        )
        monkeypatch.setattr("shared.schemas.asset_id.is_known_asset", lambda x: x == "TEST_STOCK")
        monkeypatch.setattr(
            "verification.sampled.resolve",
            lambda _: SimpleNamespace(
                timezone="America/New_York", currency="USD", registry_version="test"
            ),
        )
        opening = datetime.now(UTC).replace(second=0, microsecond=0)
        window = SessionWindow(
            calendar_id="XNYS", opens_at=opening, closes_at=opening + timedelta(hours=1)
        )
        monkeypatch.setattr("verification.sampled.resolve_window", lambda *args: window)
        policy = SampleVerification(
            pool,
            VerificationSettings(sample_mode="SHADOW", sample_calendars={"TEST_STOCK": "XNYS"}),
        )
        prediction = PredictionMade(
            correlation_id=uuid.uuid4(),
            occurred_at=opening,
            prediction_id=uuid.uuid4(),
            context_id=uuid.uuid4(),
            context_version=1,
            event_ids=[uuid.uuid4()],
            asset_id="TEST_STOCK",
            direction="UP",
            magnitude="SMALL",
            confidence=0.7,
            horizon="ONE_TRADING_DAY",
            rationale="test",
            decision_at=opening,
            decision_method="GRAPH_ONLY",
        )
        await policy.register(prediction)
        await policy.register(prediction)
        samples = []
        for i in range(4):
            stamp = opening + timedelta(minutes=15 * i)
            sample = PriceSampleObserved(
                sample_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                occurred_at=stamp,
                asset_id="TEST_STOCK",
                mapping_version="v1",
                registry_version="test",
                session=opening.date(),
                opens_at=opening,
                closes_at=window.closes_at,
                scheduled_at=stamp,
                observed_at=stamp,
                provider_quote_at=stamp,
                price="101" if i == 1 else "100",
                currency="USD",
                quote_unit="USD",
                kind="REGULAR",
                market_state="REGULAR_OPEN",
                quality="FRESH",
            )
            await policy.observe(sample)
            await policy.observe(sample)
            samples.append(sample)
        with pytest.raises(ValueError, match="conflicting"):
            await policy.observe(samples[0].model_copy(update={"currency": "EUR"}))
        await policy.tick(window.closes_at + timedelta(minutes=11))
        row = await policy.get(prediction.prediction_id)
        assert row is not None
        result = SampleResult.model_validate_json(str(row["result"]))
        assert row["status"] == "FINAL" and result.outcome == "OBSERVED_HIT"
        assert result.coverage_complete and result.observed_samples == 4
        await policy.tick(window.closes_at + timedelta(hours=2))
        unchanged = await policy.get(prediction.prediction_id)
        assert unchanged is not None and unchanged["result"] == row["result"]
        # An out-of-order superseding prediction creates a durable withdrawal tombstone.
        old = prediction.model_copy(update={"prediction_id": uuid.uuid4()})
        newer = prediction.model_copy(
            update={"prediction_id": uuid.uuid4(), "supersedes_prediction_id": old.prediction_id}
        )
        await policy.register(newer)
        await policy.register(old)
        await policy.tick(opening)
        withdrawn = await policy.get(old.prediction_id)
        assert withdrawn is not None and withdrawn["status"] == "WITHDRAWN"
        assert await pool.fetchval("SELECT to_regclass('verification.scores')") is None
    finally:
        await pool.close()
