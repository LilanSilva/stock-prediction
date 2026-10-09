from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.schemas.messages import IntradayBar, SampleProvenance

AT = datetime(2026, 10, 6, 13, 30, tzinfo=UTC)


def evidence() -> SampleProvenance:
    return SampleProvenance(
        sample_id=uuid4(),
        scheduled_at=AT,
        observed_at=AT + timedelta(seconds=5),
        expires_at=AT + timedelta(minutes=10),
        mapping_version="test-v1",
        currency="USD",
        quote_delay_seconds=900,
        quality="FRESHNESS_UNKNOWN",
    )


def test_generated_minute_preserves_original_time_and_delay_through_json() -> None:
    bar = IntradayBar(
        start=AT + timedelta(minutes=9),
        open="20.32",
        high="20.32",
        low="20.32",
        close="20.32",
        sample=evidence(),
        carried_forward=True,
    )
    restored = IntradayBar.model_validate_json(bar.model_dump_json())
    assert restored == bar
    assert restored.sample.observed_at == AT + timedelta(seconds=5)
    assert restored.sample.provider_quote_at is None
    assert restored.sample.quote_delay_seconds == 900


@pytest.mark.parametrize(
    "minute,carry,high", [(10, True, "20.32"), (0, True, "20.32"), (1, True, "21.00")]
)
def test_false_or_expired_sample_minutes_are_rejected(minute: int, carry: bool, high: str) -> None:
    with pytest.raises(ValidationError):
        IntradayBar(
            start=AT + timedelta(minutes=minute),
            open="20.32",
            high=high,
            low="20.32",
            close="20.32",
            sample=evidence(),
            carried_forward=carry,
        )


def test_carry_forward_requires_original_evidence() -> None:
    with pytest.raises(ValidationError):
        IntradayBar(start=AT, open=20, high=20, low=20, close=20, carried_forward=True)


def test_old_provider_bar_defaults_remain_genuine() -> None:
    bar = IntradayBar(start=AT, open=20, high=23, low=19, close=22)
    assert bar.sample is None and not bar.carried_forward


def test_long_expiry_cannot_hide_a_missed_collection() -> None:
    data = evidence().model_dump()
    data["expires_at"] = AT + timedelta(minutes=11)
    with pytest.raises(ValidationError):
        SampleProvenance(**data)
