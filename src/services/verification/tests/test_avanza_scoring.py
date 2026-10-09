from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from shared.schemas.messages import CloseObservation, PriceKind, PriceObserved, SampleProvenance

from tests.test_pipeline import _evaluation, _FakeRepo, _observed, _pipeline


def sampled(value: CloseObservation) -> CloseObservation:
    at = datetime.combine(value.session, datetime.min.time(), tzinfo=UTC) + timedelta(hours=19)
    data = value.model_dump()
    data.update(
        source="avanza",
        price_kind=PriceKind.AVANZA_SAMPLED_CLOSE,
        provider_bar_time=None,
        sample=SampleProvenance(
            sample_id=uuid4(),
            scheduled_at=at,
            observed_at=at + timedelta(seconds=5),
            expires_at=at + timedelta(minutes=10),
            mapping_version="v1",
            currency="USD",
            quality="FRESHNESS_UNKNOWN",
            quote_delay_seconds=900,
        ),
    )
    return CloseObservation(**data)


async def test_daily_sampled_scores_retain_distinct_policy_and_provenance() -> None:
    request_id = uuid4()
    original = _observed(request_id, "100", "103")
    message = PriceObserved(
        **{
            **original.model_dump(),
            "baseline": sampled(original.baseline),
            "settlement": sampled(original.settlement),
        }
    )
    repo = _FakeRepo(evaluation=_evaluation(request_id))
    await _pipeline(repo).process_price(message)
    assert repo.scored[0].price_policy == "AVANZA_SAMPLED_CLOSE_V1"
    assert repo.scored[0].baseline.sample == message.baseline.sample
    assert repo.scored[0].settlement.sample == message.settlement.sample
    assert repo.scored[0].actual_return == 0.03


def test_a_finance_baseline_cannot_mix_with_an_avanza_settlement() -> None:
    original = _observed(uuid4(), "100", "103")
    with pytest.raises(ValidationError, match="cannot mix"):
        PriceObserved(**{**original.model_dump(), "settlement": sampled(original.settlement)})


def test_sampled_close_cannot_be_labelled_a_provider_close() -> None:
    original = _observed(uuid4(), "100", "103")
    data = sampled(original.baseline).model_dump()
    data["price_kind"] = PriceKind.PROVIDER_DAILY_CLOSE
    with pytest.raises(ValidationError):
        CloseObservation(**data)
