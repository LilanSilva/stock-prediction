"""Deterministic graph-only decision policy (M1, zero LLM calls).

Each firing ``CAUSES`` edge is a signed force: sign from ``direction``, magnitude from the
expert-assigned ``weight`` scaled by the edge's Beta-Bernoulli reliability ``alpha/(alpha+beta)``.
Forces are summed per asset; the net-to-total ratio yields direction and confidence, and the
agreeing edges' average expert weight yields the magnitude bucket. Conflicts are recorded, not sent
to an LLM (POC-6 STOP).
"""

from __future__ import annotations

from collections.abc import Mapping

from shared.graph import FiringEdge
from shared.schemas.messages import (
    AssetId,
    ContributingEdge,
    Direction,
    EventPolarity,
    EventType,
    Magnitude,
)

from prediction.models import Decision

_ARROW = {Direction.UP: "\u2191", Direction.DOWN: "\u2193", Direction.NEUTRAL: "\u2192"}


def _flip(direction: Direction) -> Direction:
    if direction is Direction.UP:
        return Direction.DOWN
    if direction is Direction.DOWN:
        return Direction.UP
    return Direction.NEUTRAL


def _is_resolution(
    edge: FiringEdge, polarity_by_type: Mapping[EventType, EventPolarity]
) -> bool:
    """True when this edge's factor resolved (negating it).

    A propagated ``CORRELATES_WITH`` edge has no ``factor_id`` and therefore no event polarity, so
    it is never a resolution — it always keeps the graph's stored sign.
    """
    if edge.factor_id is None:
        return False
    return polarity_by_type.get(edge.factor_id) is EventPolarity.RESOLUTION


def _effective_direction(
    edge: FiringEdge, polarity_by_type: Mapping[EventType, EventPolarity]
) -> Direction:
    # A RESOLUTION event negates its factor (e.g. "war called off" turns an oil-UP edge into a
    # DOWN force); OCCURRENCE keeps the graph's stored sign.
    # Propagated edges (factor_id is None) have no polarity — always OCCURRENCE semantics.
    if _is_resolution(edge, polarity_by_type):
        return _flip(edge.direction)
    return edge.direction


def _resolved_direction(
    edge: FiringEdge, polarity_by_type: Mapping[EventType, EventPolarity], elevated: bool
) -> Direction | None:
    # Scope-B price gate: a RESOLUTION that would push the asset DOWN only counts when the price is
    # actually elevated (a risk premium to unwind). With a flat price there is nothing to revert, so
    # the edge's contribution is dropped entirely rather than counted as a DOWN force.
    direction = _effective_direction(edge, polarity_by_type)
    if _is_resolution(edge, polarity_by_type) and direction is Direction.DOWN and not elevated:
        return None
    return direction


def _contributing_edge(edge: FiringEdge, direction: Direction) -> ContributingEdge:
    return ContributingEdge(
        edge_id=edge.edge_id,
        direction=direction,
        current_weight=round(edge.reliability, 6),
        influence_weight=edge.weight,
        path=edge.edge_id,
    )


def _magnitude(avg_weight: float, small_max: float, medium_max: float) -> Magnitude:
    if avg_weight < small_max:
        return Magnitude.SMALL
    if avg_weight < medium_max:
        return Magnitude.MEDIUM
    return Magnitude.LARGE


def _factor_label(edge: FiringEdge) -> str:
    """Rationale label for an edge: its factor id, or ``CORRELATION`` for a propagated edge."""
    return edge.factor_id.value if edge.factor_id is not None else "CORRELATION"


def _rationale(
    asset_id: AssetId, direction: Direction, magnitude: Magnitude, confidence: float,
    edges: list[tuple[FiringEdge, Direction]],
) -> str:
    parts = [f"{_factor_label(e)}{_ARROW[eff]}{e.weight:.2f}" for e, eff in edges]
    text = (
        f"{asset_id.value} {direction.value} ({magnitude.value}, conf {confidence:.2f}) "
        f"from {len(edges)} causal edge(s): " + ", ".join(parts)
    )
    return text[:2000]


def decide(
    asset_id: AssetId,
    edges: list[FiringEdge],
    *,
    deadband: float,
    small_max: float,
    medium_max: float,
    polarity_by_type: Mapping[EventType, EventPolarity] | None = None,
    elevated: bool = False,
) -> Decision | None:
    """Aggregate firing edges into one decision, or ``None`` when no material edge fires.

    A material edge is one with an ``UP``/``DOWN`` direction; a set with only ``NEUTRAL`` edges (or
    none) produces no prediction (acceptance criterion 6). ``polarity_by_type`` inverts the sign of
    a factor's edges when its contributing event(s) resolved rather than occurred. ``elevated`` (the
    Scope-B price gate) suppresses a RESOLUTION-driven DOWN when the asset's price is flat: such an
    edge is dropped, and if that leaves no material edge the result is ``None``.
    """
    polarities = polarity_by_type or {}
    effective = [
        (e, eff)
        for e in edges
        if (eff := _resolved_direction(e, polarities, elevated)) is not None
    ]
    directional = [(e, eff) for e, eff in effective if eff in (Direction.UP, Direction.DOWN)]
    if not directional:
        return None

    net = 0.0
    total = 0.0
    for edge, eff in directional:
        strength = edge.weight * edge.reliability
        sign = 1.0 if eff is Direction.UP else -1.0
        net += sign * strength
        total += strength

    ratio = net / total if total > 0 else 0.0
    if abs(ratio) < deadband:
        direction = Direction.NEUTRAL
    elif ratio > 0:
        direction = Direction.UP
    else:
        direction = Direction.DOWN

    confidence = round(min(1.0, abs(ratio)), 4)

    if direction is Direction.NEUTRAL:
        agreeing = [e for e, _ in directional]
    else:
        agreeing = [e for e, eff in directional if eff is direction]
    avg_weight = sum(e.weight for e in agreeing) / len(agreeing)
    magnitude = _magnitude(avg_weight, small_max, medium_max)

    # Report every firing edge (including opposing/neutral) for explainability, using the
    # polarity-adjusted direction so the contributions sum to the net decision.
    contributing = [_contributing_edge(e, eff) for e, eff in effective]
    rationale = _rationale(asset_id, direction, magnitude, confidence, effective)
    return Decision(
        direction=direction,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        contributing_edges=contributing,
    )
