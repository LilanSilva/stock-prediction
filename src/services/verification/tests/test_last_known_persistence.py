import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from shared.schemas.messages import IntradayBar, IntradayObserved, SampleProvenance

from tests.test_intraday_persistence import (
    START,
    bars,
    observed,
    prediction,
    request_for,
)
from tests.test_intraday_persistence import (
    pool as pool,
)
from tests.test_intraday_persistence import (
    series as series,
)
from tests.test_intraday_persistence import (
    settings as settings,
)
from verification.intraday import IntradayVerification

pytestmark = pytest.mark.integration


async def test_finance_replacement_resets_sampled_baseline_without_mixing(
    pool: Any,
    series: Any,
    settings: Any,
) -> None:
    service = IntradayVerification(pool, settings)
    forecast = prediction()
    await service.register(forecast)
    request = await request_for(pool, forecast.prediction_id)
    evidence = SampleProvenance(
        sample_id=uuid4(),
        scheduled_at=START,
        observed_at=START + timedelta(seconds=5),
        expires_at=START + timedelta(minutes=10),
        mapping_version="v1",
        currency="USD",
        quality="FRESHNESS_UNKNOWN",
        quote_delay_seconds=900,
    )
    sample_bars = [
        IntradayBar(
            start=START + timedelta(minutes=i),
            open=500,
            high=500,
            low=500,
            close=500,
            sample=evidence,
            carried_forward=i > 0,
        )
        for i in range(10)
    ]
    first = observed(request, 1, sample_bars).model_copy(update={"source": "avanza"})
    await service.observe(first)
    await service.score_one(forecast.prediction_id)
    report = await service.report(forecast.prediction_id)
    assert report["result"]["baseline_price"] == "500"
    replacement = observed(request, 2, bars(), final=True).model_copy(
        update={
            "source": "yahoo",
            "replaces_previous": True,
            "fallback_reason": "MISSING_SLOT",
        }
    )
    await service.observe(IntradayObserved.model_validate_json(replacement.model_dump_json()))
    await service.observe(replacement)
    await service.score_one(forecast.prediction_id)
    report = await service.report(forecast.prediction_id)
    assert report["status"] == "SCORED"
    assert report["result"]["price_basis"] == "PROVIDER_MINUTE_V1"
    assert report["result"]["baseline_price"] == "100"
    assert report["result"]["fallback_reason"] == "MISSING_SLOT"
    assert report["result"]["target_reached"] is True
    # Provenance survives durable storage and the policy remains frozen on the evaluation.
    raw = await pool.fetchval(
        "SELECT policy FROM verification.intraday_evaluations WHERE prediction_id=$1",
        forecast.prediction_id,
    )
    assert json.loads(raw)["version"] == "LAST_KNOWN_PRICE_V1"
