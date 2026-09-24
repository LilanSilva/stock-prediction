from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from shared.schemas.messages import Direction, IntradayBar

from verification.intraday_policy import IntradayPolicy, SessionWindow, evaluate, resolve_window

START = datetime(2026, 9, 21, 15, tzinfo=UTC)
WINDOW = SessionWindow(
    calendar_id="XNYS", opens_at=START - timedelta(minutes=90), closes_at=START + timedelta(hours=5)
)
POLICY = IntradayPolicy(
    target_return=Decimal("0.01"),
    neutral_band=Decimal("0.003"),
    min_minutes=15,
    max_baseline_delay_seconds=60,
)


def bars() -> list[IntradayBar]:
    return [
        IntradayBar(start=START + timedelta(minutes=i), open=100, high=100, low=100, close=100)
        for i in range(300)
    ]


def test_post_prediction_hit_survives_reversal() -> None:
    prices = bars()
    prices[30] = prices[30].model_copy(update={"high": Decimal("102")})
    prices[-1] = prices[-1].model_copy(update={"low": Decimal("97"), "close": Decimal("98")})
    result = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=Direction.UP,
        policy=POLICY,
        bars=prices,
        final=True,
    )
    assert result.status == "SCORED"
    assert result.target_reached is True
    assert result.first_hit_bar_at == START + timedelta(minutes=30)
    assert result.closing_return == Decimal("-0.02")
    assert result.closing_correct is False


def test_pre_prediction_rise_does_not_count() -> None:
    prices = bars()
    prices.insert(
        0, IntradayBar(start=START - timedelta(minutes=30), open=100, high=110, low=100, close=100)
    )
    result = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=Direction.UP,
        policy=POLICY,
        bars=prices,
        final=True,
    )
    assert result.target_reached is False
    assert result.status == "SCORED"


def test_partial_minute_is_excluded() -> None:
    prices = bars()
    prices[0] = prices[0].model_copy(update={"high": Decimal("110")})
    result = evaluate(
        decision_at=START + timedelta(seconds=10),
        window=WINDOW,
        direction=Direction.UP,
        policy=POLICY,
        bars=prices,
        final=True,
    )
    assert result.target_reached is False
    assert result.baseline_at == START + timedelta(minutes=1)
    assert result.baseline_delay_seconds == 50


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN, Direction.NEUTRAL])
def test_gap_is_not_automatic_failure(direction: Direction) -> None:
    prices = bars()
    del prices[30]
    result = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=direction,
        policy=POLICY,
        bars=prices,
        final=True,
    )
    assert result.status == "UNSCORABLE"
    assert result.target_reached is None
    assert result.closing_return is None


def test_proven_hit_with_gap_is_diagnostic_only() -> None:
    prices = bars()
    prices[10] = prices[10].model_copy(update={"low": Decimal("98")})
    del prices[30]
    result = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=Direction.DOWN,
        policy=POLICY,
        bars=prices,
        final=True,
    )
    assert result.status == "UNSCORABLE"
    assert result.target_reached is True


def test_neutral_cannot_succeed_early_and_checks_entire_path() -> None:
    pending = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=Direction.NEUTRAL,
        policy=POLICY,
        bars=bars()[:10],
        final=False,
    )
    assert pending.target_reached is None
    prices = bars()
    prices[40] = prices[40].model_copy(update={"high": Decimal("101")})
    result = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=Direction.NEUTRAL,
        policy=POLICY,
        bars=prices,
        final=True,
    )
    assert result.target_reached is False
    assert result.closing_correct is True


def test_baseline_cannot_be_revised() -> None:
    prices = bars()
    previous = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=Direction.UP,
        policy=POLICY,
        bars=prices[:20],
        final=False,
    )
    prices[0] = prices[0].model_copy(update={"open": Decimal("99")})
    result = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=Direction.UP,
        policy=POLICY,
        bars=prices,
        final=True,
        previous=previous,
    )
    assert result.reason == "BASELINE_REVISED"
    assert result.status == "UNSCORABLE"


def test_short_window_and_delayed_baseline_are_unscorable() -> None:
    late = evaluate(
        decision_at=WINDOW.closes_at - timedelta(minutes=3),
        window=WINDOW,
        direction=Direction.UP,
        policy=POLICY,
        bars=bars(),
        final=True,
    )
    assert late.reason == "INSUFFICIENT_WINDOW"
    delayed = evaluate(
        decision_at=START,
        window=WINDOW,
        direction=Direction.UP,
        policy=POLICY,
        bars=bars()[5:],
        final=True,
    )
    assert delayed.reason == "BASELINE_TOO_LATE"


def test_calendar_holiday_early_close_and_after_hours() -> None:
    holiday = resolve_window(datetime(2026, 11, 26, 15, tzinfo=UTC), "XNYS", "America/New_York")
    assert holiday.opens_at == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    assert holiday.closes_at == datetime(2026, 11, 27, 18, tzinfo=UTC)
    after = resolve_window(holiday.closes_at, "XNYS", "America/New_York")
    assert after.opens_at == datetime(2026, 11, 30, 14, 30, tzinfo=UTC)


def test_dst_and_calendar_identity() -> None:
    before = resolve_window(datetime(2026, 3, 6, 12, tzinfo=UTC), "XNYS", "America/New_York")
    after = resolve_window(datetime(2026, 3, 9, 12, tzinfo=UTC), "XNYS", "America/New_York")
    assert before.opens_at.hour == 14
    assert after.opens_at.hour == 13
    with pytest.raises(ValueError, match="timezone"):
        resolve_window(START, "XNYS", "Europe/Stockholm")
    with pytest.raises(ValueError, match="unknown"):
        resolve_window(START, "NONEXISTENT_EXCHANGE", "America/New_York")
