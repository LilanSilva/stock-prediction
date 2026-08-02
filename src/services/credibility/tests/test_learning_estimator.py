"""Unit tests for the pure edge estimator (polarity, deadband, alpha/beta, min_samples)."""

from __future__ import annotations

import pytest
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    Direction,
    EventPolarity,
    EventType,
)

from credibility.learning.estimator import estimate_edges
from credibility.learning.models import Sample


def _sample(
    ret: float,
    *,
    polarity: EventPolarity = EventPolarity.OCCURRENCE,
    factor: EventType = EventType.MILITARY_CONFLICT,
    condition: ConditionCode | None = ConditionCode.TRANSPORT_AFFECTED,
    asset: AssetId = AssetId.BRENT_OIL,
) -> Sample:
    return Sample(
        factor=factor, condition=condition, polarity=polarity, asset=asset, actual_return=ret
    )


def test_positive_returns_yield_up_edge() -> None:
    samples = [_sample(0.03), _sample(0.02), _sample(0.04)]
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.UP
    assert estimate.sample_count == 3
    assert estimate.alpha == 4.0  # 3 agreeing + prior 1.0
    assert estimate.beta == 1.0  # 0 disagreeing + prior 1.0
    assert estimate.confidence == 1.0


def test_negative_returns_yield_down_edge() -> None:
    samples = [_sample(-0.03), _sample(-0.02), _sample(-0.05)]
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.DOWN
    assert estimate.alpha == 4.0
    assert estimate.beta == 1.0


def test_resolution_polarity_inverts_return_sign() -> None:
    # Raw returns are positive, but RESOLUTION negates them -> learned as a DOWN edge in
    # OCCURRENCE orientation.
    samples = [
        _sample(0.03, polarity=EventPolarity.RESOLUTION),
        _sample(0.02, polarity=EventPolarity.RESOLUTION),
        _sample(0.04, polarity=EventPolarity.RESOLUTION),
    ]
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.DOWN
    assert estimate.confidence == 1.0
    assert estimate.alpha == 4.0
    assert estimate.beta == 1.0


def test_mixed_polarity_aggregates_in_occurrence_orientation() -> None:
    # +2% OCCURRENCE and -2% RESOLUTION both mean "factor -> up", so they agree.
    samples = [
        _sample(0.02, polarity=EventPolarity.OCCURRENCE),
        _sample(-0.02, polarity=EventPolarity.RESOLUTION),
        _sample(0.03, polarity=EventPolarity.OCCURRENCE),
    ]
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.UP
    assert estimate.confidence == 1.0


def test_mean_within_deadband_is_neutral() -> None:
    samples = [_sample(0.001), _sample(-0.001), _sample(0.0005)]
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.NEUTRAL


def test_alpha_beta_count_agreeing_and_disagreeing() -> None:
    # Mean is +ve so direction UP; 3 positive agree, 1 negative disagrees.
    samples = [_sample(0.05), _sample(0.04), _sample(0.03), _sample(-0.01)]
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.UP
    assert estimate.alpha == 4.0  # 3 agreeing + 1
    assert estimate.beta == 2.0  # 1 disagreeing + 1
    assert estimate.confidence == 0.75


def test_group_below_min_samples_is_dropped() -> None:
    samples = [_sample(0.03), _sample(0.02)]
    assert estimate_edges(samples, deadband=0.002, min_samples=3) == []


def test_groups_are_split_by_factor_condition_and_asset() -> None:
    samples = [
        _sample(0.03, condition=ConditionCode.TRANSPORT_AFFECTED, asset=AssetId.BRENT_OIL),
        _sample(0.04, condition=ConditionCode.TRANSPORT_AFFECTED, asset=AssetId.BRENT_OIL),
        _sample(0.02, condition=ConditionCode.SAFE_HAVEN_ONLY, asset=AssetId.GOLD),
        _sample(0.03, condition=ConditionCode.SAFE_HAVEN_ONLY, asset=AssetId.GOLD),
        _sample(0.05, condition=None, asset=AssetId.BRENT_OIL),
        _sample(0.06, condition=None, asset=AssetId.BRENT_OIL),
    ]
    estimates = estimate_edges(samples, deadband=0.002, min_samples=2)
    keys = {(e.factor, e.condition, e.asset) for e in estimates}
    assert keys == {
        (EventType.MILITARY_CONFLICT, ConditionCode.TRANSPORT_AFFECTED, AssetId.BRENT_OIL),
        (EventType.MILITARY_CONFLICT, ConditionCode.SAFE_HAVEN_ONLY, AssetId.GOLD),
        (EventType.MILITARY_CONFLICT, None, AssetId.BRENT_OIL),
    }


def test_weight_is_clamped_to_unit_interval() -> None:
    samples = [_sample(0.5), _sample(0.6), _sample(0.7)]  # huge moves
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.weight == 1.0


def test_weight_scales_with_magnitude() -> None:
    samples = [_sample(0.025), _sample(0.025), _sample(0.025)]  # mean 2.5% -> 0.5 weight
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=3)
    assert 0.0 < estimate.weight < 1.0
    assert estimate.weight == pytest.approx(0.5)


def test_output_is_sorted_deterministically() -> None:
    samples = [
        _sample(0.03, factor=EventType.SANCTIONS, condition=None, asset=AssetId.GOLD),
        _sample(0.03, factor=EventType.SANCTIONS, condition=None, asset=AssetId.GOLD),
        _sample(0.03, factor=EventType.MILITARY_CONFLICT, condition=None, asset=AssetId.BRENT_OIL),
        _sample(0.03, factor=EventType.MILITARY_CONFLICT, condition=None, asset=AssetId.BRENT_OIL),
    ]
    estimates = estimate_edges(samples, deadband=0.002, min_samples=2)
    assert [e.factor for e in estimates] == [EventType.MILITARY_CONFLICT, EventType.SANCTIONS]
