from __future__ import annotations

from shared.schemas.messages import ContributingEdge, Direction

from verification.repository import _edges_to_json, _json_list


def test_json_round_trip_preserves_conditioned_edge_id() -> None:
    # The JSONB (de)serialization used for verification.evaluations.contributing_edges must keep the
    # 3-part "FACTOR|CONDITION->ASSET" edge_id verbatim (no length limit, no reformatting).
    conditioned = "MILITARY_CONFLICT|TRANSPORT_AFFECTED->XOM_NYSE"
    edge = ContributingEdge(
        edge_id=conditioned,
        direction=Direction.UP,
        current_weight=0.65,
        influence_weight=0.65,
        path=conditioned,
    )

    decoded = [
        ContributingEdge.model_validate(item) for item in _json_list(_edges_to_json([edge]))
    ]

    assert [e.edge_id for e in decoded] == [conditioned]
