from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from shared.schemas.messages import Direction, IntradayBar, SampleProvenance

from verification.intraday_policy import IntradayPolicy, SessionWindow, evaluate

OPEN = datetime(2026, 10, 6, 13, 30, tzinfo=UTC)
WINDOW = SessionWindow(calendar_id="XNYS", opens_at=OPEN, closes_at=OPEN + timedelta(minutes=30))
POLICY = IntradayPolicy(
    version="LAST_KNOWN_PRICE_V1",
    target_return="0.01",
    neutral_band="0.003",
    min_minutes=5,
    max_baseline_delay_seconds=60,
)


def sampled_minutes(prices: tuple[str, ...] = ("100", "102", "99")) -> list[IntradayBar]:
    values = []
    for index, price in enumerate(prices):
        at = OPEN + timedelta(minutes=index * 10)
        sample = SampleProvenance(
            sample_id=uuid4(),
            scheduled_at=at,
            observed_at=at + timedelta(seconds=5),
            expires_at=at + timedelta(minutes=10),
            interval_seconds=600,
            mapping_version="v1",
            currency="USD",
            quote_delay_seconds=900,
            quality="FRESHNESS_UNKNOWN",
        )
        for minute in range(10):
            values.append(
                IntradayBar(
                    start=at + timedelta(minutes=minute),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    sample=sample,
                    carried_forward=minute > 0,
                )
            )
    return values


def run(values: list[IntradayBar], decision: datetime = OPEN, **kwargs: object):
    return evaluate(
        decision_at=decision,
        window=WINDOW,
        direction=Direction.UP,
        policy=POLICY,
        bars=values,
        final=True,
        **kwargs,
    )


def test_minute_expansion_counts_three_observations_not_thirty() -> None:
    result = run(sampled_minutes())
    assert result.status == "SCORED"
    assert result.price_basis == "LAST_KNOWN_PRICE_V1"
    assert result.observed_samples == result.observed_bars == result.expected_samples == 3
    assert result.represented_minutes == 30
    assert result.target_reached is True
    assert result.first_hit_observed_at == OPEN + timedelta(minutes=10, seconds=5)
    assert result.closing_return == Decimal("-0.01")
    assert result.closing_correct is False


def test_carry_forward_before_decision_cannot_supply_new_baseline() -> None:
    result = run(sampled_minutes(), OPEN + timedelta(minutes=5))
    assert result.baseline_at == OPEN + timedelta(minutes=10, seconds=5)
    assert result.baseline_price == Decimal("102")
    assert result.observed_samples == 2


def test_missing_collection_cannot_be_hidden_by_generated_minutes() -> None:
    values = sampled_minutes()
    result = run(values[:10] + values[20:])
    assert result.status == "UNSCORABLE"
    assert result.reason == "MISSING_SAMPLES"
    assert result.target_reached is None
    assert result.closing_return is None


def test_strict_minute_policy_rejects_sampled_minutes() -> None:
    strict = POLICY.model_copy(update={"version": "INTRADAY_TARGET_V1"})
    result = evaluate(
        decision_at=OPEN,
        window=WINDOW,
        direction=Direction.UP,
        policy=strict,
        bars=sampled_minutes(),
        final=True,
    )
    assert result.reason == "UNEXPECTED_PRICE_BASIS"


def test_mixed_sources_are_rejected() -> None:
    values = sampled_minutes()
    values[0] = IntradayBar(start=OPEN, open=100, high=102, low=99, close=101)
    assert run(values).reason == "UNEXPECTED_PRICE_BASIS"


def test_finance_fallback_keeps_genuine_minute_semantics() -> None:
    values = [
        IntradayBar(start=OPEN + timedelta(minutes=i), open=100, high=100, low=100, close=100)
        for i in range(30)
    ]
    values[5] = values[5].model_copy(update={"high": Decimal("102")})
    result = run(values)
    assert result.price_basis == "PROVIDER_MINUTE_V1"
    assert result.target_reached is True
    assert result.observed_bars == 30


def test_missing_interval_does_not_turn_absence_of_hit_into_a_failure() -> None:
    result = run(sampled_minutes(("100",)))
    assert result.target_reached is None and not result.complete
