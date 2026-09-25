import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from shared.schemas.messages import Direction, PriceSampleObserved

from verification.config import VerificationSettings
from verification.intraday_policy import SessionWindow
from verification.sampled import SampleVerification
from verification.sampled_policy import SamplePolicy, evaluate_samples

OPEN = datetime(2026, 9, 24, 13, 30, tzinfo=UTC)
WINDOW = SessionWindow(calendar_id="XNYS", opens_at=OPEN, closes_at=OPEN + timedelta(hours=1))


@pytest.fixture(autouse=True)
def synthetic_registry(monkeypatch: Any) -> Any:
    monkeypatch.setattr("shared.schemas.asset_id.is_known_asset", lambda x: x == "TEST_STOCK")


def sample(i: Any, price: Any, **overrides: Any) -> Any:
    stamp = OPEN + timedelta(minutes=i * 15)
    data = dict(
        sample_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        occurred_at=stamp,
        asset_id="TEST_STOCK",
        mapping_version="v1",
        registry_version="r1",
        session=OPEN.date(),
        opens_at=OPEN,
        closes_at=WINDOW.closes_at,
        scheduled_at=stamp,
        observed_at=stamp,
        provider_quote_at=stamp,
        price=price,
        currency="USD",
        quote_unit="USD",
        kind="REGULAR",
        market_state="REGULAR_OPEN",
        quality="FRESH",
    )
    return PriceSampleObserved(**{**data, **overrides})


def evaluate(samples: Any, direction: Any = Direction.UP, start: Any = OPEN, **kwargs: Any) -> Any:
    return evaluate_samples(
        start, WINDOW, direction, SamplePolicy(), "USD", "r1", samples, final=True, **kwargs
    )


def test_hit_then_reversal_and_incomplete_coverage() -> None:
    result = evaluate([sample(0, "100"), sample(1, "101"), sample(3, "99")])
    assert result.observed_hit and result.outcome == "OBSERVED_HIT"
    assert result.last_observed_return == Decimal("-0.01")
    assert not result.coverage_complete


def test_no_hit_never_claims_continuous_failure() -> None:
    result = evaluate([sample(i, "100") for i in range(4)])
    assert result.outcome == "TARGET_NOT_OBSERVED"
    assert result.coverage_complete
    assert evaluate([sample(0, "100"), sample(2, "100")]).outcome == "INSUFFICIENT_SAMPLES"


def test_neutral_only_measures_samples() -> None:
    result = evaluate([sample(i, "100") for i in range(4)], Direction.NEUTRAL)
    assert result.outcome == "WITHIN_BAND_AT_SAMPLES"
    assert result.observed_hit is None


def test_stale_unknown_wrong_currency_and_pre_prediction_cannot_supply_baseline() -> None:
    values = [
        sample(0, "100", provider_quote_at=None, quality="FRESHNESS_UNKNOWN"),
        sample(1, "101", currency="SEK"),
        sample(2, "102", quality="STALE"),
    ]
    assert evaluate(values).outcome == "NO_BASELINE"
    old_trade = sample(1, "101", provider_quote_at=OPEN)
    assert evaluate([old_trade], start=OPEN + timedelta(minutes=14)).outcome == "NO_BASELINE"


def test_late_baseline_and_near_close_are_unscorable() -> None:
    assert evaluate([sample(2, "102")]).outcome == "BASELINE_TOO_LATE"
    assert (
        evaluate([sample(3, "102")], start=OPEN + timedelta(minutes=40)).outcome
        == "INSUFFICIENT_WINDOW"
    )


def test_mapping_change_or_baseline_revision_does_not_rewrite_accuracy() -> None:
    assert (
        evaluate([sample(0, "100"), sample(1, "101", mapping_version="v2")]).outcome
        == "MAPPING_CHANGED"
    )
    previous = evaluate([sample(0, "100"), sample(1, "101")])
    assert evaluate([sample(0, "99")], previous=previous).outcome == "BASELINE_REVISED"


@pytest.mark.asyncio
async def test_disabled_sample_policy_does_not_register() -> None:
    settings = VerificationSettings()
    assert settings.sample_mode == "OFF" and settings.sample_calendars == {}
    pool = AsyncMock()
    await SampleVerification(pool, settings).register(AsyncMock())
    pool.acquire.assert_not_called()
