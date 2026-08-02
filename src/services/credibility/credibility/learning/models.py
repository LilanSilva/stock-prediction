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
    """

    factor: EventType
    condition: ConditionCode | None
    polarity: EventPolarity
    asset: AssetId
    actual_return: float


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
