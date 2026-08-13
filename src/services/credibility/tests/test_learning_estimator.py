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
    asset: AssetId = AssetId.XOM_NYSE,
    is_abnormal: bool = False,
    asset_volatility: float = 0.0,
) -> Sample:
    return Sample(
        factor=factor,
        condition=condition,
        polarity=polarity,
        asset=asset,
        actual_return=ret,
        is_abnormal=is_abnormal,
        asset_volatility=asset_volatility,
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


def test_abnormal_samples_lower_min_samples_but_not_to_one() -> None:
    # is_abnormal lowers the bar from min_samples to abnormal_min_samples, not to a single
    # observation: the learner only creates edges now, but one observation is still too thin.
    one = [_sample(0.12, is_abnormal=True, asset_volatility=0.01)]
    assert estimate_edges(one, deadband=0.002, min_samples=5, abnormal_min_samples=2) == []

    two = [
        _sample(0.12, is_abnormal=True, asset_volatility=0.01),
        _sample(0.10, is_abnormal=True, asset_volatility=0.01),
    ]
    (estimate,) = estimate_edges(two, deadband=0.002, min_samples=5, abnormal_min_samples=2)
    assert estimate.direction is Direction.UP
    assert estimate.sample_count == 2


def test_normal_single_sample_still_dropped_by_min_samples() -> None:
    samples = [_sample(0.12, is_abnormal=False, asset_volatility=0.01)]
    assert estimate_edges(samples, deadband=0.002, min_samples=5) == []


def test_mixed_group_with_one_abnormal_sample_uses_the_lowered_min() -> None:
    # Two samples, one of them abnormal. Group total=2, effective_min=abnormal_min_samples=2.
    samples = [
        _sample(0.03, is_abnormal=False),
        _sample(0.10, is_abnormal=True, asset_volatility=0.01),
    ]
    (estimate,) = estimate_edges(samples, deadband=0.002, min_samples=5)
    assert estimate.direction is Direction.UP
    assert estimate.sample_count == 2


def test_groups_are_split_by_factor_condition_and_asset() -> None:
    samples = [
        _sample(0.03, condition=ConditionCode.TRANSPORT_AFFECTED, asset=AssetId.XOM_NYSE),
        _sample(0.04, condition=ConditionCode.TRANSPORT_AFFECTED, asset=AssetId.XOM_NYSE),
        _sample(0.02, condition=ConditionCode.SAFE_HAVEN_ONLY, asset=AssetId.NEM_NYSE),
        _sample(0.03, condition=ConditionCode.SAFE_HAVEN_ONLY, asset=AssetId.NEM_NYSE),
        _sample(0.05, condition=None, asset=AssetId.XOM_NYSE),
        _sample(0.06, condition=None, asset=AssetId.XOM_NYSE),
    ]
    estimates = estimate_edges(samples, deadband=0.002, min_samples=2)
    keys = {(e.factor, e.condition, e.asset) for e in estimates}
    assert keys == {
        (EventType.MILITARY_CONFLICT, ConditionCode.TRANSPORT_AFFECTED, AssetId.XOM_NYSE),
        (EventType.MILITARY_CONFLICT, ConditionCode.SAFE_HAVEN_ONLY, AssetId.NEM_NYSE),
        (EventType.MILITARY_CONFLICT, None, AssetId.XOM_NYSE),
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
        _sample(0.03, factor=EventType.SANCTIONS, condition=None, asset=AssetId.NEM_NYSE),
        _sample(0.03, factor=EventType.SANCTIONS, condition=None, asset=AssetId.NEM_NYSE),
        _sample(0.03, factor=EventType.MILITARY_CONFLICT, condition=None, asset=AssetId.XOM_NYSE),
        _sample(0.03, factor=EventType.MILITARY_CONFLICT, condition=None, asset=AssetId.XOM_NYSE),
    ]
    estimates = estimate_edges(samples, deadband=0.002, min_samples=2)
    assert [e.factor for e in estimates] == [EventType.MILITARY_CONFLICT, EventType.SANCTIONS]


# --- estimate_correlation_edges tests ---

from credibility.learning.estimator import estimate_correlation_edges  # noqa: E402
from credibility.learning.models import CorrelationSample  # noqa: E402


def _corr_sample(
    ret: float,
    *,
    source: AssetId = AssetId.XOM_NYSE,
    condition: ConditionCode = ConditionCode.UPSTREAM_UP,
    target: AssetId = AssetId.NEM_NYSE,
    is_abnormal: bool = False,
    asset_volatility: float = 0.0,
) -> CorrelationSample:
    return CorrelationSample(
        source_asset=source,
        condition=condition,
        target_asset=target,
        actual_return=ret,
        is_abnormal=is_abnormal,
        asset_volatility=asset_volatility,
    )


def test_corr_positive_returns_yield_up_edge() -> None:
    samples = [_corr_sample(0.03), _corr_sample(0.02), _corr_sample(0.04)]
    (estimate,) = estimate_correlation_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.UP
    assert estimate.sample_count == 3
    assert estimate.alpha == 4.0  # 3 agreeing + prior 1.0
    assert estimate.beta == 1.0


def test_corr_negative_returns_yield_down_edge() -> None:
    samples = [_corr_sample(-0.03), _corr_sample(-0.02), _corr_sample(-0.05)]
    (estimate,) = estimate_correlation_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.DOWN
    assert estimate.alpha == 4.0
    assert estimate.beta == 1.0


def test_corr_neutral_group_is_not_dropped_but_returns_neutral_direction() -> None:
    samples = [_corr_sample(0.001), _corr_sample(-0.001), _corr_sample(0.0005)]
    (estimate,) = estimate_correlation_edges(samples, deadband=0.002, min_samples=3)
    assert estimate.direction is Direction.NEUTRAL


def test_corr_below_min_samples_is_dropped() -> None:
    samples = [_corr_sample(0.03), _corr_sample(0.02)]
    assert estimate_correlation_edges(samples, deadband=0.002, min_samples=3) == []


def test_corr_abnormal_samples_lower_min_samples_but_not_to_one() -> None:
    one = [_corr_sample(0.15, is_abnormal=True, asset_volatility=0.01)]
    assert (
        estimate_correlation_edges(
            one, deadband=0.002, min_samples=5, abnormal_min_samples=2
        )
        == []
    )

    two = [
        _corr_sample(0.15, is_abnormal=True, asset_volatility=0.01),
        _corr_sample(0.12, is_abnormal=True, asset_volatility=0.01),
    ]
    (estimate,) = estimate_correlation_edges(
        two, deadband=0.002, min_samples=5, abnormal_min_samples=2
    )
    assert estimate.direction is Direction.UP
    assert estimate.sample_count == 2


def test_corr_groups_split_by_source_condition_target() -> None:
    samples = [
        _corr_sample(0.03, source=AssetId.XOM_NYSE, target=AssetId.NEM_NYSE),
        _corr_sample(0.04, source=AssetId.XOM_NYSE, target=AssetId.NEM_NYSE),
        _corr_sample(-0.02, source=AssetId.XOM_NYSE, target=AssetId.LUG_STO),
        _corr_sample(-0.03, source=AssetId.XOM_NYSE, target=AssetId.LUG_STO),
    ]
    estimates = estimate_correlation_edges(samples, deadband=0.002, min_samples=2)
    keys = {(e.source_asset, e.condition, e.target_asset) for e in estimates}
    assert keys == {
        (AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP, AssetId.NEM_NYSE),
        (AssetId.XOM_NYSE, ConditionCode.UPSTREAM_UP, AssetId.LUG_STO),
    }


def test_corr_output_sorted_deterministically() -> None:
    samples = [
        _corr_sample(0.03, target=AssetId.NEM_NYSE),
        _corr_sample(0.03, target=AssetId.NEM_NYSE),
        _corr_sample(-0.03, target=AssetId.LUG_STO, condition=ConditionCode.UPSTREAM_DOWN),
        _corr_sample(-0.03, target=AssetId.LUG_STO, condition=ConditionCode.UPSTREAM_DOWN),
    ]
    estimates = estimate_correlation_edges(samples, deadband=0.002, min_samples=2)
    # UPSTREAM_DOWN < UPSTREAM_UP alphabetically
    assert estimates[0].condition is ConditionCode.UPSTREAM_DOWN
    assert estimates[1].condition is ConditionCode.UPSTREAM_UP
