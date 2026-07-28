"""Deterministic graph-only decision policy (M1, zero LLM calls).

Each firing ``CAUSES`` edge is a signed force: sign from ``direction``, magnitude from the
expert-assigned ``weight`` scaled by the edge's Beta-Bernoulli reliability ``alpha/(alpha+beta)``.
Forces are summed per asset; the net-to-total ratio yields direction and confidence, and the
agreeing edges' average expert weight yields the magnitude bucket. Conflicts are recorded, not sent
to an LLM (POC-6 STOP).
"""

from __future__ import annotations

from shared.graph import FiringEdge
from shared.schemas.messages import AssetId, ContributingEdge, Direction, Magnitude

from prediction.models import Decision

_ARROW = {Direction.UP: "\u2191", Direction.DOWN: "\u2193", Direction.NEUTRAL: "\u2192"}


def _contributing_edge(edge: FiringEdge) -> ContributingEdge:
    return ContributingEdge(
        edge_id=edge.edge_id,
        direction=edge.direction,
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


def _rationale(
    asset_id: AssetId, direction: Direction, magnitude: Magnitude, confidence: float,
    edges: list[FiringEdge],
) -> str:
    parts = [f"{e.factor_id.value}{_ARROW[e.direction]}{e.weight:.2f}" for e in edges]
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
) -> Decision | None:
    """Aggregate firing edges into one decision, or ``None`` when no material edge fires.

    A material edge is one with an ``UP``/``DOWN`` direction; a set with only ``NEUTRAL`` edges (or
    none) produces no prediction (acceptance criterion 6).
    """
    directional = [e for e in edges if e.direction in (Direction.UP, Direction.DOWN)]
    if not directional:
        return None

    net = 0.0
    total = 0.0
    for edge in directional:
        strength = edge.weight * edge.reliability
        sign = 1.0 if edge.direction == Direction.UP else -1.0
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
        agreeing = directional
    else:
        agreeing = [e for e in directional if e.direction == direction]
    avg_weight = sum(e.weight for e in agreeing) / len(agreeing)
    magnitude = _magnitude(avg_weight, small_max, medium_max)

    # Report every firing edge (including opposing/neutral) for explainability.
    contributing = [_contributing_edge(e) for e in edges]
    rationale = _rationale(asset_id, direction, magnitude, confidence, edges)
    return Decision(
        direction=direction,
        magnitude=magnitude,
        confidence=confidence,
        rationale=rationale,
        contributing_edges=contributing,
    )
