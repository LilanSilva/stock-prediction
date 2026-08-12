"""Immutable value objects shared by the offline learning batch.

Kept free of any I/O so ``estimator`` can import them without pulling in database drivers.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.schemas.messages import AssetId, ConditionCode, Direction, EventPolarity, EventType


@dataclass(frozen=True, slots=True)
class Sample:
    """One realised observation: an event's factor/condition and the asset's next-day return.

    ``condition`` is ``None`` for the unconditional (factor-only) observation; ``polarity`` is
    applied to ``actual_return`` by the estimator, not here, so the raw market return is preserved.

    ``is_abnormal`` is True when the absolute return exceeds ``abnormal_threshold`` multiples of
    the asset's historical daily volatility, indicating the move is unlikely to be random noise.
    ``asset_volatility`` is the standard deviation of daily returns over the volatility lookback
    window; 0.0 when fewer than two closes were available (treated as normal by the estimator).
    """

    factor: EventType
    condition: ConditionCode | None
    polarity: EventPolarity
    asset: AssetId
    actual_return: float
    is_abnormal: bool = False
    asset_volatility: float = 0.0


@dataclass(frozen=True, slots=True)
class EdgeEstimate:
    """A data-derived conditioned-edge proposal for one (factor, condition, asset) group."""

    factor: EventType
    condition: ConditionCode | None
    asset: AssetId
    direction: Direction
    weight: float
    confidence: float
    alpha: float
    beta: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class CorrelationSample:
    """One realised observation for a CORRELATES_WITH edge.

    ``source_asset``  — the asset that was predicted (propagation_depth=0).
    ``condition``     — UPSTREAM_UP when the source was predicted UP, UPSTREAM_DOWN when DOWN.
    ``target_asset``  — the asset whose actual return is being observed.
    ``actual_return`` — (settlement_close - baseline_close) / baseline_close for target_asset
                        in the same settlement session as the source prediction.
    ``is_abnormal``   — True when |actual_return| >= abnormal_threshold * target volatility.
    ``asset_volatility`` — std dev of target_asset daily returns over the volatility window;
                           0.0 when fewer than two closes available.
    """

    source_asset: AssetId
    condition: ConditionCode          # always UPSTREAM_UP or UPSTREAM_DOWN
    target_asset: AssetId
    actual_return: float
    is_abnormal: bool = False
    asset_volatility: float = 0.0


@dataclass(frozen=True, slots=True)
class CorrelationEdgeEstimate:
    """A data-derived CORRELATES_WITH edge proposal.

    One estimate per ``(source_asset, condition, target_asset)`` group.
    """

    source_asset: AssetId
    condition: ConditionCode
    target_asset: AssetId
    direction: Direction
    weight: float
    confidence: float
    alpha: float
    beta: float
    sample_count: int
