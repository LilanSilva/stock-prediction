"""Pure estimation math for the offline structure learner (no I/O; fully unit-tested).

Given realised :class:`Sample` observations it derives one :class:`EdgeEstimate` per
``(factor, condition, asset)`` group. Two POC-grade constants are documented below.
"""

from __future__ import annotations

from collections import defaultdict

from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    Direction,
    EventPolarity,
    EventType,
)

from credibility.learning.models import EdgeEstimate, Sample

# A 5% mean signed daily move maps to full edge weight 1.0; smaller moves scale linearly. Chosen as
# a simple, transparent POC scaling — daily gold/oil moves rarely exceed a few percent.
_WEIGHT_RETURN_SCALE = 0.05

# Beta(1, 1) uninformed prior, matching the seed and online-consumer convention.
_PRIOR = 1.0

_GroupKey = tuple[EventType, ConditionCode | None, AssetId]


def _signed_return(sample: Sample) -> float:
    """Return the market return re-oriented to OCCURRENCE semantics.

    A RESOLUTION event (e.g. a strike called off) is the negation of the factor, so its observed
    return is inverted before aggregation; edges are therefore always learned in the OCCURRENCE
    orientation used by the seed and prediction gating.
    """
    if sample.polarity is EventPolarity.RESOLUTION:
        return -sample.actual_return
    return sample.actual_return


def estimate_edges(
    samples: list[Sample], *, deadband: float, min_samples: int
) -> list[EdgeEstimate]:
    """Aggregate ``samples`` into conditioned-edge estimates.

    Groups by ``(factor, condition, asset)``; groups with fewer than ``min_samples`` observations
    are dropped as insufficient evidence — unless any sample in the group is flagged
    ``is_abnormal=True``, in which case a single observation is sufficient. Direction is UP/DOWN
    when the mean signed return clears ``±deadband``, else NEUTRAL. ``alpha``/``beta`` are the
    agreeing/disagreeing counts plus the Beta(1, 1) prior, and ``confidence`` is the agreeing
    fraction. Output is sorted for determinism.
    """
    grouped: dict[_GroupKey, list[Sample]] = defaultdict(list)
    for sample in samples:
        grouped[(sample.factor, sample.condition, sample.asset)].append(sample)

    estimates: list[EdgeEstimate] = []
    for (factor, condition, asset), group_samples in grouped.items():
        total = len(group_samples)
        effective_min = 1 if any(s.is_abnormal for s in group_samples) else min_samples
        if total < effective_min:
            continue

        signed = [_signed_return(s) for s in group_samples]

        mean = sum(signed) / total
        positives = sum(1 for value in signed if value > 0.0)
        negatives = sum(1 for value in signed if value < 0.0)

        if mean > deadband:
            direction = Direction.UP
            agreeing = positives
        elif mean < -deadband:
            direction = Direction.DOWN
            agreeing = negatives
        else:
            direction = Direction.NEUTRAL
            agreeing = 0

        disagreeing = total - agreeing
        estimates.append(
            EdgeEstimate(
                factor=factor,
                condition=condition,
                asset=asset,
                direction=direction,
                weight=min(1.0, abs(mean) / _WEIGHT_RETURN_SCALE),
                confidence=agreeing / total,
                alpha=agreeing + _PRIOR,
                beta=disagreeing + _PRIOR,
                sample_count=total,
            )
        )

    estimates.sort(
        key=lambda e: (e.factor.value, e.condition.value if e.condition else "", e.asset.value)
    )
    return estimates
