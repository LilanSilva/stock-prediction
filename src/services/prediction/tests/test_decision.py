from __future__ import annotations

from shared.graph import FiringEdge
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    Direction,
    EventPolarity,
    EventType,
    Magnitude,
)

from prediction.decision import decide

_KW = {"deadband": 0.15, "small_max": 0.40, "medium_max": 0.70}


def _edge(
    factor: EventType,
    direction: Direction,
    weight: float,
    *,
    asset: AssetId = AssetId.NEM_NYSE,
    alpha: float = 1.0,
    beta: float = 1.0,
    condition: ConditionCode | None = None,
) -> FiringEdge:
    return FiringEdge(
        factor_id=factor,
        asset_id=asset,
        direction=direction,
        weight=weight,
        confidence=0.7,
        alpha=alpha,
        beta=beta,
        condition=condition,
    )


def test_no_directional_edges_yields_no_prediction() -> None:
    assert decide(AssetId.NEM_NYSE, [], **_KW) is None
    neutral = [_edge(EventType.OTHER, Direction.NEUTRAL, 0.5)]
    assert decide(AssetId.NEM_NYSE, neutral, **_KW) is None


def test_unanimous_up_is_confident_and_large() -> None:
    edges = [
        _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75),
        _edge(EventType.SANCTIONS, Direction.UP, 0.80),
    ]
    decision = decide(AssetId.NEM_NYSE, edges, **_KW)
    assert decision is not None
    assert decision.direction == Direction.UP
    assert decision.confidence == 1.0
    assert decision.magnitude == Magnitude.LARGE  # avg agreeing weight 0.775 >= 0.70
    assert len(decision.contributing_edges) == 2


def test_balanced_conflict_falls_in_deadband_and_is_neutral() -> None:
    edges = [
        _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75),
        _edge(EventType.RATE_DECISION, Direction.DOWN, 0.60),
    ]
    decision = decide(AssetId.NEM_NYSE, edges, **_KW)
    assert decision is not None
    # net=0.375-0.30=0.075, total=0.675 -> ratio 0.111 < 0.15 deadband
    assert decision.direction == Direction.NEUTRAL


def test_dominant_force_wins_conflict() -> None:
    edges = [
        _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75),
        _edge(EventType.RATE_DECISION, Direction.DOWN, 0.40),
    ]
    decision = decide(AssetId.NEM_NYSE, edges, **_KW)
    assert decision is not None
    assert decision.direction == Direction.UP
    # Both edges still reported for explainability (incl the opposing one).
    assert len(decision.contributing_edges) == 2
    directions = {e.direction for e in decision.contributing_edges}
    assert directions == {Direction.UP, Direction.DOWN}


def test_magnitude_buckets() -> None:
    small = decide(AssetId.NEM_NYSE, [_edge(EventType.POLITICAL_TRANSITION, Direction.UP, 0.35)], **_KW)
    medium = decide(AssetId.NEM_NYSE, [_edge(EventType.INFLATION_CHANGE, Direction.UP, 0.50)], **_KW)
    large_edges = [_edge(EventType.SUPPLY_DISRUPTION, Direction.UP, 0.85)]
    large = decide(AssetId.XOM_NYSE, large_edges, **_KW)
    assert small is not None and small.magnitude == Magnitude.SMALL
    assert medium is not None and medium.magnitude == Magnitude.MEDIUM
    assert large is not None and large.magnitude == Magnitude.LARGE


def test_contributing_edge_reports_reliability_and_weight() -> None:
    edge = _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75, alpha=3.0, beta=1.0)
    decision = decide(AssetId.NEM_NYSE, [edge], **_KW)
    assert decision is not None
    contributing = decision.contributing_edges[0]
    assert contributing.edge_id == "MILITARY_CONFLICT->NEM_NYSE"
    assert contributing.current_weight == 0.75  # reliability alpha/(alpha+beta)
    assert contributing.influence_weight == 0.75  # expert magnitude
    assert "MILITARY_CONFLICT" in decision.rationale


def test_resolution_polarity_flips_edge_sign() -> None:
    edge = _edge(
        EventType.MILITARY_CONFLICT,
        Direction.UP,
        0.65,
        asset=AssetId.XOM_NYSE,
        condition=ConditionCode.TRANSPORT_AFFECTED,
    )
    polarities = {EventType.MILITARY_CONFLICT: EventPolarity.RESOLUTION}
    # The Scope-B gate requires an elevated price for a RESOLUTION-driven DOWN to apply.
    decision = decide(
        AssetId.XOM_NYSE, [edge], **_KW, polarity_by_type=polarities, elevated=True
    )
    assert decision is not None
    assert decision.direction == Direction.DOWN
    contributing = decision.contributing_edges[0]
    # Reported sign follows the flipped force; the edge identity is unchanged.
    assert contributing.direction == Direction.DOWN
    assert contributing.edge_id == "MILITARY_CONFLICT|TRANSPORT_AFFECTED->XOM_NYSE"


def test_occurrence_polarity_leaves_sign_unchanged() -> None:
    edge = _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.65, asset=AssetId.XOM_NYSE)
    polarities = {EventType.MILITARY_CONFLICT: EventPolarity.OCCURRENCE}
    decision = decide(AssetId.XOM_NYSE, [edge], **_KW, polarity_by_type=polarities)
    assert decision is not None
    assert decision.direction == Direction.UP


def test_resolution_down_suppressed_when_not_elevated() -> None:
    edge = _edge(
        EventType.MILITARY_CONFLICT,
        Direction.UP,
        0.65,
        asset=AssetId.XOM_NYSE,
        condition=ConditionCode.TRANSPORT_AFFECTED,
    )
    polarities = {EventType.MILITARY_CONFLICT: EventPolarity.RESOLUTION}
    # Flat price: the flipped-to-DOWN edge is dropped, leaving no material edge -> no prediction.
    decision = decide(
        AssetId.XOM_NYSE, [edge], **_KW, polarity_by_type=polarities, elevated=False
    )
    assert decision is None


def test_resolution_down_applies_when_elevated() -> None:
    edge = _edge(
        EventType.MILITARY_CONFLICT,
        Direction.UP,
        0.65,
        asset=AssetId.XOM_NYSE,
        condition=ConditionCode.TRANSPORT_AFFECTED,
    )
    polarities = {EventType.MILITARY_CONFLICT: EventPolarity.RESOLUTION}
    decision = decide(
        AssetId.XOM_NYSE, [edge], **_KW, polarity_by_type=polarities, elevated=True
    )
    assert decision is not None
    assert decision.direction == Direction.DOWN
    assert decision.contributing_edges[0].direction == Direction.DOWN


def test_occurrence_up_is_unaffected_by_elevated_state() -> None:
    edge = _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.65, asset=AssetId.XOM_NYSE)
    polarities = {EventType.MILITARY_CONFLICT: EventPolarity.OCCURRENCE}
    not_elevated = decide(
        AssetId.XOM_NYSE, [edge], **_KW, polarity_by_type=polarities, elevated=False
    )
    elevated = decide(
        AssetId.XOM_NYSE, [edge], **_KW, polarity_by_type=polarities, elevated=True
    )
    assert not_elevated is not None and not_elevated.direction == Direction.UP
    assert elevated is not None and elevated.direction == Direction.UP
