"""Deterministic close-to-close scoring (verification functional document sec 4)."""

from __future__ import annotations

from decimal import Decimal

from shared.schemas.messages import Direction, Magnitude

from verification.models import ScoreOutcome


def score(
    *,
    baseline_close: Decimal,
    settlement_close: Decimal,
    predicted_direction: Direction,
    deadband: float,
    magnitude_medium_min: float,
    magnitude_large_min: float,
) -> ScoreOutcome:
    """Score one evaluation from its two immutable closes.

    ``actual_return = (settlement - baseline) / baseline``. Moves within the deadband are NEUTRAL;
    magnitude buckets split absolute return at the medium/large thresholds. ``is_correct`` is true
    only when predicted and actual directions match; ``score`` is 1.0/0.0.
    """
    actual_return = float((settlement_close - baseline_close) / baseline_close)
    magnitude_abs = abs(actual_return)

    if magnitude_abs < deadband:
        actual_direction = Direction.NEUTRAL
    elif actual_return > 0:
        actual_direction = Direction.UP
    else:
        actual_direction = Direction.DOWN

    if magnitude_abs < magnitude_medium_min:
        actual_magnitude = Magnitude.SMALL
    elif magnitude_abs < magnitude_large_min:
        actual_magnitude = Magnitude.MEDIUM
    else:
        actual_magnitude = Magnitude.LARGE

    is_correct = predicted_direction == actual_direction
    return ScoreOutcome(
        actual_return=actual_return,
        actual_direction=actual_direction,
        actual_magnitude=actual_magnitude,
        is_correct=is_correct,
        score=1.0 if is_correct else 0.0,
    )
