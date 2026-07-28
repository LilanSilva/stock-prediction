from __future__ import annotations

from decimal import Decimal

from shared.schemas.messages import Direction, Magnitude

from verification.models import ScoreOutcome
from verification.scoring import score

_KW = {"deadband": 0.003, "magnitude_medium_min": 0.01, "magnitude_large_min": 0.03}


def _score(baseline: str, settlement: str, predicted: Direction = Direction.UP) -> ScoreOutcome:
    return score(
        baseline_close=Decimal(baseline),
        settlement_close=Decimal(settlement),
        predicted_direction=predicted,
        **_KW,
    )


def test_move_within_deadband_is_neutral() -> None:
    # +0.2% is inside the ±0.3% deadband.
    outcome = _score("100.00", "100.20")
    assert outcome.actual_direction == Direction.NEUTRAL
    assert outcome.actual_magnitude == Magnitude.SMALL
    assert outcome.is_correct is False  # predicted UP, actual NEUTRAL


def test_up_small() -> None:
    outcome = _score("100.00", "100.50")  # +0.5% -> UP, SMALL (<1%)
    assert outcome.actual_direction == Direction.UP
    assert outcome.actual_magnitude == Magnitude.SMALL
    assert outcome.is_correct is True
    assert outcome.score == 1.0


def test_up_medium() -> None:
    outcome = _score("100.00", "102.00")  # +2% -> UP, MEDIUM (1%-3%)
    assert outcome.actual_direction == Direction.UP
    assert outcome.actual_magnitude == Magnitude.MEDIUM


def test_down_large() -> None:
    outcome = _score("100.00", "96.00", predicted=Direction.DOWN)  # -4% -> DOWN, LARGE (>=3%)
    assert outcome.actual_direction == Direction.DOWN
    assert outcome.actual_magnitude == Magnitude.LARGE
    assert outcome.is_correct is True


def test_wrong_direction_scores_zero() -> None:
    outcome = _score("100.00", "97.00", predicted=Direction.UP)  # -3% actual DOWN vs predicted UP
    assert outcome.actual_direction == Direction.DOWN
    assert outcome.is_correct is False
    assert outcome.score == 0.0


def test_return_is_close_to_close_ratio() -> None:
    outcome = _score("50.00", "51.50")  # +3% exactly -> LARGE
    assert round(outcome.actual_return, 4) == 0.03
    assert outcome.actual_magnitude == Magnitude.LARGE
